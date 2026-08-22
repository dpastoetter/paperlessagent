"""Cooperative cancel/retry for in-flight per-file ingest."""

from __future__ import annotations

import asyncio

_file_cancel_event: asyncio.Event | None = None
_file_cancel_id: str | None = None
_file_cancel_path: str | None = None


class FileCancelledError(Exception):
    """Raised when the user cancels processing of the active file."""


def bind_file_cancel(file_id: str, source_path: str) -> asyncio.Event:
    """Begin a cancel scope for one file in the active ingest job."""
    global _file_cancel_event, _file_cancel_id, _file_cancel_path
    _file_cancel_event = asyncio.Event()
    _file_cancel_id = file_id
    _file_cancel_path = source_path
    return _file_cancel_event


def clear_file_cancel() -> None:
    global _file_cancel_event, _file_cancel_id, _file_cancel_path
    _file_cancel_event = None
    _file_cancel_id = None
    _file_cancel_path = None


def get_active_file_id() -> str | None:
    return _file_cancel_id


def get_active_file_path() -> str | None:
    return _file_cancel_path


def get_file_cancel_event() -> asyncio.Event | None:
    return _file_cancel_event


def is_file_cancelled() -> bool:
    return _file_cancel_event is not None and _file_cancel_event.is_set()


def request_cancel_file(file_id: str) -> bool:
    """Request cancel for the file currently bound to the cancel scope."""
    if _file_cancel_id != file_id or _file_cancel_event is None:
        return False
    if _file_cancel_event.is_set():
        return True
    _file_cancel_event.set()
    return True


def raise_if_cancelled() -> None:
    if is_file_cancelled():
        raise FileCancelledError("File processing was cancelled")
