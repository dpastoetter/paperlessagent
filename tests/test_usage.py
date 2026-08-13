"""Tests for process-lifetime LLM usage counters."""

from __future__ import annotations

import asyncio

import pytest

from paperless_agent import config, llm
from paperless_agent.llm import complete_text
from paperless_agent.usage import (
    normalize_gemini_usage,
    normalize_ollama_usage,
    normalize_openai_usage,
    record_usage,
    reset_usage,
    usage_snapshot,
)


@pytest.fixture(autouse=True)
def _clear_usage():
    reset_usage()
    yield
    reset_usage()


def test_record_usage_aggregates():
    record_usage("openai", "gpt-5", prompt_tokens=10, completion_tokens=5, kind="chat")
    record_usage("openai", "text-embedding-3-small", prompt_tokens=20, kind="embed")
    snap = usage_snapshot()
    assert snap["requests"] == 2
    assert snap["chat_requests"] == 1
    assert snap["embed_requests"] == 1
    assert snap["prompt_tokens"] == 30
    assert snap["completion_tokens"] == 5
    assert snap["total_tokens"] == 35
    assert snap["last_provider"] == "openai"
    assert snap["last_kind"] == "embed"
    assert snap["updated_at"]


def test_normalize_helpers():
    assert normalize_openai_usage({"prompt_tokens": 3, "completion_tokens": 2}) == (
        3,
        2,
        5,
    )
    assert normalize_openai_usage({"input_tokens": 4, "output_tokens": 1}) == (4, 1, 5)
    assert normalize_gemini_usage(
        {"prompt_token_count": 7, "candidates_token_count": 3, "total_token_count": 10}
    ) == (7, 3, 10)
    assert normalize_ollama_usage({"prompt_eval_count": 11, "eval_count": 9}) == (
        11,
        9,
        20,
    )


def test_complete_ollama_records_usage(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(
        llm,
        "ensure_ollama_ready",
        lambda **_k: {"ready": True},
    )
    monkeypatch.setattr(llm, "resolve_runtime_model", lambda wanted, **_k: "gemma3:4b")

    async def fake_request(payload, *, cancel_event=None, timeout=None):
        return {
            "message": {"role": "assistant", "content": "ok"},
            "prompt_eval_count": 12,
            "eval_count": 4,
        }

    monkeypatch.setattr(llm, "_ollama_request", fake_request)
    result = asyncio.run(complete_text("hi", instructions="sys"))
    assert result == "ok"
    snap = usage_snapshot()
    assert snap["requests"] == 1
    assert snap["prompt_tokens"] == 12
    assert snap["completion_tokens"] == 4
    assert snap["total_tokens"] == 16
    assert snap["last_model"] == "gemma3:4b"


def test_diagnostics_includes_usage(client):
    record_usage("openai", "gpt-5", prompt_tokens=100, completion_tokens=20)
    resp = client.get("/api/diagnostics")
    assert resp.status_code == 200
    body = resp.json()
    assert "usage" in body
    assert body["usage"]["requests"] == 1
    assert body["usage"]["total_tokens"] == 120


def test_health_is_minimal(client):
    record_usage("openai", "gpt-5", prompt_tokens=100, completion_tokens=20)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "usage" not in body
    assert "auth" not in body
    assert "llm_provider" not in body
    assert "model" not in body
    assert "embedding_model" not in body
    assert "cloud_disclaimer" not in body
    assert "ollama" not in body
