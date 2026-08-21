"""LLM provider interface and runner helpers."""

from __future__ import annotations

import asyncio

from paperless_agent import runner
from paperless_agent.job_control import FileCancelledError
from paperless_agent.providers import get_llm_provider
from paperless_agent.providers.base import LlmProvider
from paperless_agent.providers.runtime import ActiveLlmProvider


def test_get_llm_provider_returns_protocol_impl():
    provider = get_llm_provider()
    assert isinstance(provider, ActiveLlmProvider)
    assert isinstance(provider, LlmProvider)
    assert provider.name
    usage = provider.usage()
    assert "requests" in usage or "embed_requests" in usage or isinstance(usage, dict)


def test_active_provider_delegates_text_and_vision(monkeypatch):
    calls: list[str] = []

    async def fake_text(prompt, *, instructions, cancel_event=None, json_mode=False):
        calls.append("text")
        return "ok-text"

    async def fake_vision(
        prompt,
        *,
        images,
        instructions,
        mime_type="image/png",
        cancel_event=None,
        timeout=None,
        ollama_options=None,
    ):
        calls.append("vision")
        return "ok-vision"

    monkeypatch.setattr(
        "paperless_agent.llm.complete_text_via_backend",
        fake_text,
    )
    monkeypatch.setattr(
        "paperless_agent.llm.complete_with_images_via_backend",
        fake_vision,
    )
    monkeypatch.setattr(
        "paperless_agent.tools.rag_index.embed_texts",
        lambda texts: [[0.1, 0.2] for _ in texts],
    )

    provider = ActiveLlmProvider()
    assert asyncio.run(provider.complete_text("hi", instructions="sys")) == "ok-text"
    assert (
        asyncio.run(provider.complete_vision("read", images=[b"x"], instructions="sys"))
        == "ok-vision"
    )
    assert provider.embed_texts(["a"]) == [[0.1, 0.2]]
    assert calls == ["text", "vision"]
    health = provider.health()
    assert health["provider"] == provider.name
    assert "model" in health


def test_run_pipeline_shapes_success_and_errors(monkeypatch):
    async def ok(_path: str):
        return {
            "status": "success",
            "filename": "a.pdf",
            "archive_path": "/tmp/a.pdf",
            "document_id": "d1",
        }

    monkeypatch.setattr(runner, "ingest_document", ok)
    out = asyncio.run(runner.run_pipeline_on_path("/tmp/in.pdf"))
    assert out["status"] == "success"
    assert "Filed" in out["reply"]

    async def pending(_path: str):
        return {"status": "pending_review", "message": "queued"}

    monkeypatch.setattr(runner, "ingest_document", pending)
    out = asyncio.run(runner.run_pipeline_on_path("/tmp/in.pdf"))
    assert out["status"] == "pending_review"

    async def fail(_path: str):
        return {"status": "error", "error": "boom"}

    monkeypatch.setattr(runner, "ingest_document", fail)
    out = asyncio.run(runner.run_pipeline_on_path("/tmp/in.pdf"))
    assert out["status"] == "error"
    assert "boom" in out["reply"]

    async def cancelled(_path: str):
        raise FileCancelledError("stopped")

    monkeypatch.setattr(runner, "ingest_document", cancelled)
    out = asyncio.run(runner.run_pipeline_on_path("/tmp/in.pdf"))
    assert out["status"] == "cancelled"


def test_run_query_delegates_to_ask(monkeypatch):
    async def fake_ask(q: str, history=None):
        return {"status": "success", "reply": q}

    monkeypatch.setattr(runner, "ask_archive", fake_ask)
    out = asyncio.run(runner.run_query("hello"))
    assert out["reply"] == "hello"
