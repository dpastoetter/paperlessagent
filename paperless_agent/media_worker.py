"""Run untrusted PDF/image parse/render work in a resource-limited subprocess."""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paperless_agent import config

logger = logging.getLogger(__name__)


class MediaWorkerError(RuntimeError):
    """Native media worker failed, timed out, or was killed by resource limits."""

    def __init__(self, message: str, *, code: str = "worker_failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MediaWorkerLimits:
    timeout_s: float
    memory_bytes: int
    cpu_seconds: int


def _default_limits() -> MediaWorkerLimits:
    return MediaWorkerLimits(
        timeout_s=float(config.MEDIA_WORKER_TIMEOUT_S),
        memory_bytes=max(64 * 1024 * 1024, int(config.MEDIA_WORKER_MEMORY_MB) * 1024 * 1024),
        cpu_seconds=max(1, int(config.MEDIA_WORKER_CPU_S)),
    )


def _apply_resource_limits(limits: MediaWorkerLimits) -> None:
    """Best-effort CPU/address-space caps (Linux/macOS; no-op when unsupported)."""
    try:
        import resource
    except ImportError:
        return
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds + 1))
    except (ValueError, OSError, AttributeError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
    except (ValueError, OSError, AttributeError):
        pass
    # Prefer failing fast on decompression bombs inside the worker too.
    try:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = max(1, int(config.MEDIA_MAX_IMAGE_PIXELS))
    except Exception:  # noqa: BLE001
        pass


def _worker_main(job: str, payload: dict[str, Any], conn: Any, limits: MediaWorkerLimits) -> None:
    _apply_resource_limits(limits)
    try:
        if job == "extract_pdf_page_texts":
            from pypdf import PdfReader

            reader = PdfReader(str(payload["path"]), strict=False)
            texts = [(page.extract_text() or "").strip() for page in reader.pages]
            conn.send(("ok", texts))
        elif job == "render_pdf_page":
            from pdf2image import convert_from_path

            images = convert_from_path(
                str(payload["path"]),
                dpi=int(payload["dpi"]),
                first_page=int(payload["page_index"]),
                last_page=int(payload["page_index"]),
                fmt="png",
            )
            if not images:
                raise RuntimeError(f"failed to render page {payload['page_index']}")
            img = images[0]
            if img.mode != "RGB":
                img = img.convert("RGB")
            # Pipe PNG bytes back — keeps parent process free of Poppler handles.
            import io

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            conn.send(("ok", buf.getvalue()))
        elif job == "load_image_rgb_png":
            from PIL import Image

            with Image.open(payload["path"]) as img:
                rgb = img.convert("RGB")
                import io

                buf = io.BytesIO()
                rgb.save(buf, format="PNG")
                conn.send(("ok", buf.getvalue()))
        else:
            raise ValueError(f"unknown media worker job: {job}")
    except Exception as exc:  # noqa: BLE001 — surface to parent
        conn.send(("err", f"{type(exc).__name__}: {exc}"))
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def run_media_job(
    job: str,
    payload: dict[str, Any],
    *,
    limits: MediaWorkerLimits | None = None,
) -> Any:
    """
    Execute ``job`` in a child process with CPU/memory/time limits.

    Returns the job result payload, or raises ``MediaWorkerError``.
    """
    effective = limits or _default_limits()
    ctx = mp.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(
        target=_worker_main,
        args=(job, payload, child_conn, effective),
        daemon=True,
    )
    proc.start()
    child_conn.close()
    proc.join(effective.timeout_s)
    if proc.is_alive():
        proc.terminate()
        proc.join(2.0)
        if proc.is_alive():
            proc.kill()
            proc.join(1.0)
        raise MediaWorkerError(
            f"media worker timed out after {effective.timeout_s:.0f}s ({job})",
            code="timeout",
        )

    if parent_conn.poll(0.1):
        status, body = parent_conn.recv()
    else:
        exit_code = proc.exitcode
        raise MediaWorkerError(
            f"media worker exited without result (job={job}, exit={exit_code})",
            code="worker_failed",
        )
    parent_conn.close()

    if status == "ok":
        return body
    raise MediaWorkerError(str(body), code="worker_failed")


def extract_pdf_page_texts_isolated(path: Path | str) -> list[str]:
    """Extract text layers via a resource-limited worker."""
    return list(
        run_media_job(
            "extract_pdf_page_texts",
            {"path": str(Path(path).resolve())},
        )
    )


def render_pdf_page_png_isolated(path: Path | str, page_index: int, *, dpi: int) -> bytes:
    """Rasterize one PDF page with Poppler inside the media worker."""
    return bytes(
        run_media_job(
            "render_pdf_page",
            {
                "path": str(Path(path).resolve()),
                "page_index": int(page_index),
                "dpi": int(dpi),
            },
        )
    )


def load_image_rgb_png_isolated(path: Path | str) -> bytes:
    """Decode an image to RGB PNG bytes inside the media worker."""
    return bytes(
        run_media_job(
            "load_image_rgb_png",
            {"path": str(Path(path).resolve())},
        )
    )


def media_worker_enabled() -> bool:
    """Allow tests / constrained environments to disable subprocess isolation."""
    return os.getenv("PAPERLESS_MEDIA_WORKER", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
