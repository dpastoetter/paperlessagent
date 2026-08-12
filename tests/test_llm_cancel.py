"""Tests for cooperative LLM cancellation (Ollama long requests)."""

from __future__ import annotations

import asyncio

import pytest

from paperless_agent.job_control import FileCancelledError, bind_file_cancel, request_cancel_file
from paperless_agent.llm import _ollama_request, run_cancellable


@pytest.fixture(autouse=True)
def _reset_cancel_scope():
    from paperless_agent.job_control import clear_file_cancel

    clear_file_cancel()
    yield
    clear_file_cancel()


def test_run_cancellable_raises_when_event_set():
    async def exercise():
        started = asyncio.Event()

        async def slow():
            started.set()
            await asyncio.sleep(60)
            return "done"

        event = asyncio.Event()
        bind_file_cancel("file-a", "/tmp/a.pdf")
        task = asyncio.create_task(run_cancellable(slow(), cancel_event=event, timeout=120))
        await started.wait()
        event.set()
        request_cancel_file("file-a")

        with pytest.raises(FileCancelledError):
            await asyncio.wait_for(task, timeout=5)

    asyncio.run(exercise())


def test_ollama_request_timeout_has_clear_message(monkeypatch):
    import httpx

    monkeypatch.setattr("paperless_agent.llm.config.OLLAMA_BASE_URL", "http://127.0.0.1:11434")

    async def slow_post(*_args, **_kwargs):
        await asyncio.sleep(0.01)
        raise httpx.ReadTimeout("")

    monkeypatch.setattr(httpx.AsyncClient, "post", slow_post)

    async def exercise():
        with pytest.raises(RuntimeError, match="timed out after 42s"):
            await _ollama_request({"model": "gemma3", "messages": []}, timeout=42)

    asyncio.run(exercise())


def test_ollama_request_aborts_when_cancel_event_set(monkeypatch):
    import httpx

    monkeypatch.setattr("paperless_agent.llm.config.OLLAMA_BASE_URL", "http://127.0.0.1:11434")

    async def slow_post(*_args, **_kwargs):
        await asyncio.sleep(60)
        return httpx.Response(200, json={"message": {"content": "hi"}})

    monkeypatch.setattr(httpx.AsyncClient, "post", slow_post)

    async def exercise():
        event = asyncio.Event()
        bind_file_cancel("file-a", "/tmp/a.pdf")
        task = asyncio.create_task(
            _ollama_request({"model": "gemma3", "messages": []}, cancel_event=event)
        )
        await asyncio.sleep(0.05)
        event.set()
        request_cancel_file("file-a")

        with pytest.raises(FileCancelledError):
            await asyncio.wait_for(task, timeout=5)

    asyncio.run(exercise())
