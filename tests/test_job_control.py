"""Tests for per-file ingest cancellation."""

from __future__ import annotations

import pytest

from paperless_agent.job_control import (
    FileCancelledError,
    bind_file_cancel,
    clear_file_cancel,
    get_active_file_id,
    is_file_cancelled,
    raise_if_cancelled,
    request_cancel_file,
)


@pytest.fixture(autouse=True)
def _reset_cancel_scope():
    clear_file_cancel()
    yield
    clear_file_cancel()


def test_request_cancel_only_active_file():
    bind_file_cancel("file-a", "/tmp/a.pdf")
    assert request_cancel_file("file-a") is True
    assert is_file_cancelled() is True
    assert request_cancel_file("file-b") is False


def test_raise_if_cancelled():
    bind_file_cancel("file-a", "/tmp/a.pdf")
    request_cancel_file("file-a")
    with pytest.raises(FileCancelledError):
        raise_if_cancelled()


def test_clear_file_cancel():
    bind_file_cancel("file-a", "/tmp/a.pdf")
    clear_file_cancel()
    assert get_active_file_id() is None
    assert request_cancel_file("file-a") is False
