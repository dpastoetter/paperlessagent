"""Inbox processing and background polling for new scans."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from paperless_agent.progress import (
    current_file_id,
    current_job_id,
    new_file_id,
    new_job_id,
    publish,
)
from paperless_agent.review import pending_source_paths
from paperless_agent.runner import run_pipeline_on_path
from paperless_agent.settings import get_batch_settings, load_settings
from paperless_agent.tools.filesystem import list_inbox

logger = logging.getLogger(__name__)

_process_lock = asyncio.Lock()


async def process_inbox() -> dict[str, Any]:
    """Process all supported files currently in the configured source folder."""
    async with _process_lock:
        inbox = list_inbox()
        all_files = inbox.get("files", [])
        awaiting = pending_source_paths()
        files = [f for f in all_files if f.get("path") not in awaiting]
        held_for_review = len(all_files) - len(files)
        job_id = new_job_id()
        job_token = current_job_id.set(job_id)

        if not files:
            if held_for_review:
                message = (
                    f"{held_for_review} file(s) awaiting review — nothing new to process"
                )
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
                file_token = current_file_id.set(file_id)
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
                try:
                    result = await run_pipeline_on_path(item["path"])
                    entry: dict[str, Any] = {
                        "path": item["path"],
                        "result": result,
                        "file_id": file_id,
                        "filename": filename,
                    }
                    if result.get("status") not in {
                        "success",
                        "partial",
                        "pending_review",
                    }:
                        entry["error"] = result.get("reply") or result.get(
                            "result", {}
                        ).get("error", "ingest failed")
                    results.append(entry)
                    if entry.get("error"):
                        file_status = "error"
                    elif result.get("status") == "pending_review":
                        file_status = "review"
                    else:
                        file_status = "done"
                    await publish(
                        {
                            "type": "file_finished",
                            "job_id": job_id,
                            "file_id": file_id,
                            "filename": filename,
                            "path": item["path"],
                            "status": file_status,
                            "detail": entry.get("error")
                            or result.get("reply")
                            or result.get("filename"),
                            "index": index,
                            "total": len(files),
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    results.append(
                        {
                            "path": item["path"],
                            "error": str(exc),
                            "file_id": file_id,
                            "filename": filename,
                        }
                    )
                    await publish(
                        {
                            "type": "file_finished",
                            "job_id": job_id,
                            "file_id": file_id,
                            "filename": filename,
                            "path": item["path"],
                            "status": "error",
                            "detail": str(exc),
                            "index": index,
                            "total": len(files),
                        }
                    )
                finally:
                    current_file_id.reset(file_token)

            errors = sum(1 for r in results if r.get("error"))
            status = "success" if errors == 0 else "partial"
            await publish(
                {
                    "type": "job_finished",
                    "job_id": job_id,
                    "status": status,
                    "processed": len(results),
                    "error_count": errors,
                    "total": len(files),
                    "source_dir": inbox.get("source_dir"),
                }
            )
            return {
                "status": status,
                "processed": len(results),
                "error_count": errors,
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
                result = await process_inbox()
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
