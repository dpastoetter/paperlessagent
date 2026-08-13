"""Inbox worker: process_single_file, cancel, retry (pipeline stubbed)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from paperless_agent import inbox_worker
from paperless_agent.job_control import FileCancelledError
from paperless_agent.review import create_review
from paperless_agent.settings import get_source_dir


@pytest.fixture()
def inbox_pdf(isolated_data):
    path = get_source_dir() / "scan.pdf"
    path.write_bytes(b"%PDF-1.4 stub")
    return path


def test_process_single_file_success(isolated_data, inbox_pdf, monkeypatch):
    async def fake_pipeline(path: str):
        return {"status": "success", "filename": "out.pdf", "path": path}

    monkeypatch.setattr(inbox_worker, "run_pipeline_on_path", fake_pipeline)
    result = asyncio.run(inbox_worker.process_single_file(str(inbox_pdf)))
    assert result["status"] == "success"
    assert result["filename"] == "out.pdf"


def test_process_single_file_skips_pending_review(isolated_data, inbox_pdf, monkeypatch):
    create_review(
        source_path=str(inbox_pdf.resolve()),
        original_name="scan.pdf",
        proposal={"filename": "x.pdf", "doc_type": "other"},
    )

    async def boom(_path: str):
        raise AssertionError("pipeline must not run for pending review")

    monkeypatch.setattr(inbox_worker, "run_pipeline_on_path", boom)
    result = asyncio.run(inbox_worker.process_single_file(str(inbox_pdf)))
    assert result["status"] == "pending_review"


def test_process_single_file_cancelled(isolated_data, inbox_pdf, monkeypatch):
    async def cancelled(_path: str):
        raise FileCancelledError("File processing was cancelled")

    monkeypatch.setattr(inbox_worker, "run_pipeline_on_path", cancelled)
    result = asyncio.run(inbox_worker.process_single_file(str(inbox_pdf)))
    assert result["status"] == "cancelled"
    assert "cancelled" in result["message"].lower()


def test_cancel_active_file_when_idle():
    result = asyncio.run(inbox_worker.cancel_active_file("nope"))
    assert result["status"] == "error"
    assert "no ingest" in result["error"].lower()


def test_cancel_active_file_wrong_id(isolated_data, inbox_pdf, monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_pipeline(_path: str):
        started.set()
        await release.wait()
        return {"status": "success"}

    monkeypatch.setattr(inbox_worker, "run_pipeline_on_path", slow_pipeline)

    async def scenario():
        task = asyncio.create_task(inbox_worker.process_single_file(str(inbox_pdf)))
        await started.wait()
        wrong = await inbox_worker.cancel_active_file("not-the-active-id")
        assert wrong["status"] == "error"
        assert "does not match" in wrong["error"]
        release.set()
        await task

    asyncio.run(scenario())


def test_retry_file_when_idle(isolated_data, inbox_pdf, monkeypatch):
    calls: list[str] = []

    async def fake_pipeline(path: str):
        calls.append(path)
        return {"status": "success", "path": path}

    monkeypatch.setattr(inbox_worker, "run_pipeline_on_path", fake_pipeline)
    result = asyncio.run(inbox_worker.retry_file(str(inbox_pdf)))
    assert result["status"] == "success"
    assert calls


def test_retry_while_other_file_processing(isolated_data, inbox_pdf, monkeypatch):
    other = get_source_dir() / "other.pdf"
    other.write_bytes(b"%PDF other")
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_pipeline(path: str):
        if Path(path).name == "scan.pdf":
            started.set()
            await release.wait()
        return {"status": "success", "path": path}

    monkeypatch.setattr(inbox_worker, "run_pipeline_on_path", slow_pipeline)

    async def scenario():
        task = asyncio.create_task(inbox_worker.process_single_file(str(inbox_pdf)))
        await started.wait()
        blocked = await inbox_worker.retry_file(str(other))
        assert blocked["status"] == "error"
        assert "another file" in blocked["error"].lower()
        release.set()
        await task

    asyncio.run(scenario())


def test_is_processing_false_when_idle():
    assert inbox_worker.is_processing() is False
