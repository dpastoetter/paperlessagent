"""Inbox processing and background polling for new scans."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from deepcatalog.job_control import (
    FileCancelledError,
    bind_file_cancel,
    clear_file_cancel,
    get_active_file_id,
    get_active_file_path,
    request_cancel_file,
)
from deepcatalog.progress import (
    current_file_id,
    current_job_id,
    new_file_id,
    new_job_id,
    publish,
)
from deepcatalog.review import pending_source_paths
from deepcatalog.runner import run_pipeline_on_path
from deepcatalog.settings import get_batch_settings, load_settings
from deepcatalog.tools.filesystem import confined_inbox_file, list_inbox

logger = logging.getLogger(__name__)

_process_lock = asyncio.Lock()


def is_processing() -> bool:
    """True while an ingest run holds the processing lock."""
    return _process_lock.locked()


async def process_single_file(path: str) -> dict[str, Any]:
    """Process one file under the shared lock (no overlap with inbox runs)."""
    async with _process_lock:
        if path in pending_source_paths():
            return {
                "status": "pending_review",
                "message": "file is already awaiting review",
            }
        file_id = new_file_id()
        job_id = new_job_id()
        filename = Path(path).name
        job_token = current_job_id.set(job_id)
        file_token = current_file_id.set(file_id)
        bind_file_cancel(file_id, path)
        await publish(
            {
                "type": "job_started",
                "job_id": job_id,
                "total": 1,
                "files": [{"file_id": file_id, "filename": filename, "path": path}],
            }
        )
        await publish(
            {
                "type": "file_started",
                "job_id": job_id,
                "file_id": file_id,
                "filename": filename,
                "path": path,
                "index": 0,
                "total": 1,
            }
        )
        try:
            result = await run_pipeline_on_path(path)
        except FileCancelledError as exc:
            result = {
                "status": "cancelled",
                "message": str(exc),
                "path": path,
                "file_id": file_id,
            }
        finally:
            clear_file_cancel()
            current_file_id.reset(file_token)
            current_job_id.reset(job_token)

        if result.get("status") == "cancelled":
            file_status = "cancelled"
        elif result.get("status") == "pending_review":
            file_status = "review"
        elif result.get("status") not in {"success", "partial"}:
            file_status = "error"
        else:
            file_status = "done"
        await publish(
            {
                "type": "file_finished",
                "job_id": job_id,
                "file_id": file_id,
                "filename": filename,
                "path": path,
                "status": file_status,
                "detail": result.get("reply") or result.get("message") or result.get("error"),
                "index": 0,
                "total": 1,
            }
        )
        await publish(
            {
                "type": "job_finished",
                "job_id": job_id,
                "status": "success" if file_status in {"done", "review"} else "partial",
                "processed": 1,
                "total": 1,
            }
        )
        if result.get("status") == "cancelled":
            return {
                "status": "cancelled",
                "message": result.get("message", "File processing was cancelled"),
                "path": path,
                "file_id": file_id,
            }
        return result


async def cancel_active_file(file_id: str) -> dict[str, Any]:
    """Request cancellation of the file currently being processed."""
    if not is_processing():
        return {"status": "error", "error": "no ingest job is running"}
    if not request_cancel_file(file_id):
        active = get_active_file_id()
        if active is None:
            return {"status": "error", "error": "no file is actively processing"}
        return {
            "status": "error",
            "error": f"file_id does not match the active file ({active})",
        }
    return {
        "status": "success",
        "file_id": file_id,
        "message": "Cancellation requested",
    }


async def retry_file(path: str) -> dict[str, Any]:
    """
    Re-process one inbox file.

    When another file is running, returns 409 unless the path matches the active
    file (cancel is requested; retry again once the job finishes).
    """
    try:
        resolved = str(confined_inbox_file(path))
    except ValueError:
        return {
            "status": "error",
            "error": "only files inside the configured inbox can be processed",
        }
    if is_processing():
        active_path = get_active_file_path()
        if active_path and Path(active_path).resolve() == Path(resolved):
            file_id = get_active_file_id()
            if file_id:
                await cancel_active_file(file_id)
            return {
                "status": "success",
                "cancelled": True,
                "message": (
                    "Cancellation requested for the active file. "
                    "Click Retry again once the job finishes."
                ),
            }
        return {
            "status": "error",
            "error": "another file is processing — cancel it first or wait",
        }
    return await process_single_file(resolved)


async def _process_one_file(
    *,
    job_id: str,
    item: dict[str, Any],
    queued: dict[str, Any],
    index: int,
    total: int,
) -> dict[str, Any]:
    file_id = queued["file_id"]
    filename = queued["filename"]
    source_path = item["path"]
    file_token = current_file_id.set(file_id)
    bind_file_cancel(file_id, source_path)
    entry: dict[str, Any] = {
        "path": source_path,
        "file_id": file_id,
        "filename": filename,
    }
    try:
        result = await run_pipeline_on_path(source_path)
        entry["result"] = result
        if result.get("status") == "cancelled":
            entry["status"] = "cancelled"
        elif result.get("status") not in {
            "success",
            "partial",
            "pending_review",
        }:
            raw_error = result.get("reply") or result.get("result", {}).get(
                "error", "ingest failed"
            )
            entry["error"] = raw_error if isinstance(raw_error, str) else str(raw_error)
        if entry.get("status") != "cancelled":
            if entry.get("error"):
                file_status = "error"
            elif result.get("status") == "pending_review":
                file_status = "review"
            else:
                file_status = "done"
        else:
            file_status = "cancelled"
        await publish(
            {
                "type": "file_finished",
                "job_id": job_id,
                "file_id": file_id,
                "filename": filename,
                "path": source_path,
                "status": file_status,
                "detail": entry.get("error")
                or result.get("reply")
                or result.get("message")
                or result.get("filename"),
                "index": index,
                "total": total,
            }
        )
    except FileCancelledError as exc:
        entry["status"] = "cancelled"
        entry["error"] = str(exc)
        await publish(
            {
                "type": "file_finished",
                "job_id": job_id,
                "file_id": file_id,
                "filename": filename,
                "path": source_path,
                "status": "cancelled",
                "detail": str(exc),
                "index": index,
                "total": total,
            }
        )
    except Exception as exc:  # noqa: BLE001
        entry["error"] = str(exc)
        await publish(
            {
                "type": "file_finished",
                "job_id": job_id,
                "file_id": file_id,
                "filename": filename,
                "path": source_path,
                "status": "error",
                "detail": str(exc),
                "index": index,
                "total": total,
            }
        )
    finally:
        clear_file_cancel()
        current_file_id.reset(file_token)
    return entry


async def process_inbox(min_age_seconds: float | None = None) -> dict[str, Any]:
    """
    Process all supported files currently in the configured source folder.

    min_age_seconds, when set, skips files modified more recently than that —
    used by the background poller so half-copied scans are left for the next
    cycle instead of being ingested truncated.
    """
    async with _process_lock:
        inbox = list_inbox()
        all_files = inbox.get("files", [])
        awaiting = pending_source_paths()
        files = [f for f in all_files if f.get("path") not in awaiting]
        held_for_review = len(all_files) - len(files)
        if min_age_seconds is not None:
            cutoff = time.time() - min_age_seconds
            files = [f for f in files if (f.get("mtime") or 0) <= cutoff]
        job_id = new_job_id()
        job_token = current_job_id.set(job_id)

        if not files:
            if held_for_review:
                message = f"{held_for_review} file(s) awaiting review — nothing new to process"
            else:
                message = "Inbox is empty — upload a scan first"
            await publish(
                {
                    "type": "job_finished",
                    "job_id": job_id,
                    "status": "empty",
                    "processed": 0,
                    "total": 0,
                    "message": message,
                    "source_dir": inbox.get("source_dir"),
                }
            )
            current_job_id.reset(job_token)
            return {
                "status": "empty",
                "processed": 0,
                "results": [],
                "message": message,
                "source_dir": inbox.get("source_dir"),
            }

        queued_files = []
        for item in files:
            queued_files.append(
                {
                    "file_id": new_file_id(),
                    "filename": item.get("name") or Path(item["path"]).name,
                    "path": item.get("path"),
                    "status": "queued",
                }
            )

        await publish(
            {
                "type": "job_started",
                "job_id": job_id,
                "total": len(files),
                "source_dir": inbox.get("source_dir"),
                "files": queued_files,
            }
        )

        results: list[dict[str, Any]] = []
        try:
            for index, (item, queued) in enumerate(zip(files, queued_files, strict=True)):
                file_id = queued["file_id"]
                filename = queued["filename"]
                await publish(
                    {
                        "type": "file_started",
                        "job_id": job_id,
                        "file_id": file_id,
                        "filename": filename,
                        "path": item["path"],
                        "index": index,
                        "total": len(files),
                    }
                )
                entry = await _process_one_file(
                    job_id=job_id,
                    item=item,
                    queued=queued,
                    index=index,
                    total=len(files),
                )
                results.append(entry)

            errors = sum(1 for r in results if r.get("error") and r.get("status") != "cancelled")
            cancelled = sum(1 for r in results if r.get("status") == "cancelled")
            status = "success" if errors == 0 else "partial"
            await publish(
                {
                    "type": "job_finished",
                    "job_id": job_id,
                    "status": status,
                    "processed": len(results),
                    "error_count": errors,
                    "cancelled_count": cancelled,
                    "total": len(files),
                    "source_dir": inbox.get("source_dir"),
                }
            )
            return {
                "status": status,
                "processed": len(results),
                "error_count": errors,
                "cancelled_count": cancelled,
                "source_dir": inbox.get("source_dir"),
                "batch": get_batch_settings(),
                "results": results,
                "job_id": job_id,
            }
        finally:
            current_job_id.reset(job_token)


async def inbox_poll_loop(stop_event: asyncio.Event) -> None:
    """
    Periodically scan the inbox and process new files.

    Interval comes from Setup → batch.poll_interval_seconds.
    0 (or negative) disables automatic scanning; settings are re-read each cycle.
    """
    logger.info("Inbox poller started")
    while not stop_event.is_set():
        load_settings(reload=True)
        batch = get_batch_settings()
        try:
            interval = float(batch.get("poll_interval_seconds") or 0)
        except (TypeError, ValueError):
            interval = 0

        if interval > 0:
            try:
                # Skip files still being copied in (e.g. by a network scanner).
                result = await process_inbox(min_age_seconds=5.0)
                processed = int(result.get("processed") or 0)
                if processed:
                    logger.info(
                        "Inbox poll processed %s file(s) (status=%s)",
                        processed,
                        result.get("status"),
                    )
            except Exception:  # noqa: BLE001
                logger.exception("Inbox poll cycle failed")
            wait_for = max(1.0, interval)
        else:
            # Auto-scan off; wake periodically to pick up settings changes.
            wait_for = 5.0

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=wait_for)
        except asyncio.TimeoutError:
            continue

    logger.info("Inbox poller stopped")
