"""Tests for the Ollama provider: chat routing, vision payloads, embeddings."""

from __future__ import annotations

import asyncio
import base64

import pytest

from paperless_agent import llm
from paperless_agent.llm import complete_text, complete_with_images
from paperless_agent.tools import rag_index
from paperless_agent.tools.rag_index import embed_texts


@pytest.fixture()
def ollama_provider(monkeypatch):
    """Force the ollama provider and capture chat payloads instead of HTTP."""
    monkeypatch.setattr("paperless_agent.llm.LLM_PROVIDER", "ollama")
    monkeypatch.setattr("paperless_agent.tools.rag_index.LLM_PROVIDER", "ollama")
    captured: list[dict] = []

    async def fake_request(payload):
        captured.append(payload)
        return {"message": {"role": "assistant", "content": "  transcribed text  "}}

    monkeypatch.setattr(llm, "_ollama_request", fake_request)
    return captured


def test_complete_text_routes_to_ollama(ollama_provider):
    result = asyncio.run(
        complete_text("classify this document", instructions="You are a classifier.")
    )
    assert result == "transcribed text"

    payload = ollama_provider[0]
    assert payload["stream"] is False
    assert payload["messages"][0] == {
        "role": "system",
        "content": "You are a classifier.",
    }
    assert payload["messages"][1]["content"] == "classify this document"
    assert "images" not in payload["messages"][1]


def test_complete_with_images_sends_base64_pages(ollama_provider):
    pages = [b"\x89PNG fake page 1", b"\x89PNG fake page 2"]
    result = asyncio.run(
        complete_with_images(
            "transcribe these pages",
            images=pages,
            instructions="You are an OCR engine.",
        )
    )
    assert result == "transcribed text"

    message = ollama_provider[0]["messages"][1]
    assert message["images"] == [
        base64.b64encode(raw).decode("ascii") for raw in pages
    ]


def test_embed_texts_routes_to_ollama(monkeypatch):
    monkeypatch.setattr("paperless_agent.tools.rag_index.LLM_PROVIDER", "ollama")
    posted: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"embeddings": [[0.1, 0.2], [0.3, 0.4]]}

    def fake_post(url, *, json, timeout):
        posted["url"] = url
        posted["json"] = json
        return FakeResponse()

    monkeypatch.setattr(rag_index.httpx, "post", fake_post)

    vectors = embed_texts(["chunk one", "chunk two"])
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert posted["url"].endswith("/api/embed")
    assert posted["json"]["input"] == ["chunk one", "chunk two"]


def test_embed_texts_rejects_bad_response_shape(monkeypatch):
    monkeypatch.setattr("paperless_agent.tools.rag_index.LLM_PROVIDER", "ollama")

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"embeddings": [[0.1]]}  # one vector for two inputs

    monkeypatch.setattr(
        rag_index.httpx, "post", lambda url, *, json, timeout: FakeResponse()
    )

    with pytest.raises(RuntimeError, match="Unexpected embedding response"):
        embed_texts(["a", "b"])
