"""Tests for the Ollama provider: chat routing, vision payloads, embeddings, setup."""

from __future__ import annotations

import asyncio
import base64

import httpx
import pytest

from paperless_agent import config, llm
from paperless_agent.llm import complete_text, complete_with_images
from paperless_agent.ollama_setup import (
    apply_llm_provider,
    enable_ollama,
    format_http_error,
    missing_models,
    model_name_matches,
    ollama_status,
    upsert_env_values,
)
from paperless_agent.tools import rag_index
from paperless_agent.tools.rag_index import embed_texts


@pytest.fixture()
def ollama_provider(monkeypatch):
    """Force the ollama provider and capture chat payloads instead of HTTP."""
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
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
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "nomic-embed-text")
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
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")

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


def test_model_name_matching():
    assert model_name_matches("gemma3:latest", "gemma3")
    assert model_name_matches("nomic-embed-text", "nomic-embed-text")
    assert not model_name_matches("llama3.2", "gemma3")


def test_missing_models_detects_chat_and_embed():
    assert missing_models([], chat="gemma3", embed="nomic-embed-text") == [
        "gemma3",
        "nomic-embed-text",
    ]
    assert missing_models(["gemma3:latest"], chat="gemma3", embed="nomic-embed-text") == [
        "nomic-embed-text"
    ]
    assert (
        missing_models(
            ["gemma3:latest", "nomic-embed-text:latest"],
            chat="gemma3",
            embed="nomic-embed-text",
        )
        == []
    )


def test_format_http_error_suggests_pull():
    msg = format_http_error(
        httpx.HTTPStatusError(
            "404",
            request=httpx.Request("POST", "http://localhost/api/chat"),
            response=httpx.Response(404, text="model 'gemma3' not found"),
        ),
        model="gemma3",
    )
    assert "ollama pull gemma3" in msg


def test_upsert_env_values_preserves_comments(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# keep me\nDATA_DIR=./data\nPAPERLESS_LLM_PROVIDER=openai\n")
    upsert_env_values(
        {
            "PAPERLESS_LLM_PROVIDER": "ollama",
            "PAPERLESS_MODEL": "gemma3",
        },
        path=path,
    )
    text = path.read_text()
    assert "# keep me" in text
    assert "DATA_DIR=./data" in text
    assert "PAPERLESS_LLM_PROVIDER=ollama" in text
    assert "PAPERLESS_MODEL=gemma3" in text
    assert text.count("PAPERLESS_LLM_PROVIDER=") == 1


def test_enable_ollama_updates_runtime_and_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.setattr("paperless_agent.ollama_setup.env_path", lambda: env_file)
    monkeypatch.setattr(
        "paperless_agent.ollama_setup.probe_ollama",
        lambda *_a, **_k: {
            "reachable": True,
            "base_url": "http://localhost:11434",
            "models": ["gemma3:latest", "nomic-embed-text:latest"],
            "version": "0.6.0",
            "error": None,
        },
    )

    result = enable_ollama(persist=True)
    assert result["applied"]["provider"] == "ollama"
    assert config.LLM_PROVIDER == "ollama"
    assert config.MODEL_NAME == "gemma3"
    assert config.EMBEDDING_MODEL == "nomic-embed-text"
    assert "PAPERLESS_LLM_PROVIDER=ollama" in env_file.read_text()
    assert result["ollama"]["ready"] is True


def test_apply_llm_provider_rejects_unknown():
    with pytest.raises(ValueError, match="provider must be"):
        apply_llm_provider("azure")


def test_ollama_status_api(client, monkeypatch):
    monkeypatch.setattr(
        "app.main.ollama_status",
        lambda **_k: {
            "active": False,
            "ready": False,
            "reachable": False,
            "base_url": "http://localhost:11434",
            "missing_models": ["gemma3", "nomic-embed-text"],
            "pull_command": "ollama pull gemma3 && ollama pull nomic-embed-text",
            "error": "Cannot reach Ollama",
            "install_hint": "Install Ollama",
        },
    )
    resp = client.get("/api/ollama/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["ollama"]["reachable"] is False


def test_ollama_enable_api(client, monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    monkeypatch.setattr("paperless_agent.ollama_setup.env_path", lambda: env_file)
    monkeypatch.setattr(
        "paperless_agent.ollama_setup.probe_ollama",
        lambda *_a, **_k: {
            "reachable": True,
            "base_url": "http://localhost:11434",
            "models": ["gemma3:latest"],
            "version": "0.6.0",
            "error": None,
        },
    )
    resp = client.post("/api/ollama/enable", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"]["provider"] == "ollama"
    assert "nomic-embed-text" in body["ollama"]["missing_models"]


def test_llm_provider_api_switches_back(client, monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    monkeypatch.setattr("paperless_agent.ollama_setup.env_path", lambda: env_file)
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    assert client.post("/api/privacy/cloud-disclaimer", json={"accepted": True}).status_code == 200
    resp = client.post("/api/llm/provider", json={"provider": "openai"})
    assert resp.status_code == 200
    assert resp.json()["applied"]["provider"] == "openai"
    assert config.LLM_PROVIDER == "openai"
