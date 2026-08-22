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
    clear_ollama_tags_cache,
    current_compute_label,
    enable_ollama,
    ensure_ollama_ready,
    env_path,
    format_http_error,
    infer_model_processor,
    missing_models,
    model_name_matches,
    ollama_status,
    resolve_installed_model,
    start_ollama,
    summarize_compute,
    upsert_env_values,
)
from paperless_agent.progress import llm_busy_detail
from paperless_agent.tools import rag_index
from paperless_agent.tools.rag_index import embed_texts


@pytest.fixture()
def ollama_provider(monkeypatch):
    """Force the ollama provider and capture chat payloads instead of HTTP."""
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(
        llm,
        "ensure_ollama_ready",
        lambda **_k: {
            "ready": True,
            "reachable": True,
            "listening": True,
            "base_url": "http://localhost:11434",
        },
    )
    captured: list[dict] = []

    async def fake_request(payload, *, cancel_event=None, timeout=None):
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
    assert message["images"] == [base64.b64encode(raw).decode("ascii") for raw in pages]


def test_embed_texts_routes_to_ollama(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setattr(
        rag_index,
        "ensure_ollama_ready",
        lambda **_k: {"ready": True, "reachable": True, "listening": True},
    )
    posted: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"embeddings": [[0.1, 0.2], [0.3, 0.4]]}

    def fake_post(url, *, json, timeout, **_kwargs):
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
    monkeypatch.setattr(
        rag_index,
        "ensure_ollama_ready",
        lambda **_k: {"ready": True, "reachable": True, "listening": True},
    )

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"embeddings": [[0.1]]}  # one vector for two inputs

    monkeypatch.setattr(
        rag_index.httpx,
        "post",
        lambda url, *, json, timeout, **_kwargs: FakeResponse(),
    )

    with pytest.raises(RuntimeError, match="Unexpected embedding response"):
        embed_texts(["a", "b"])


def test_ensure_ollama_ready_skips_non_ollama(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    assert ensure_ollama_ready()["skipped"] is True


def test_ensure_ollama_ready_raises_when_offline(monkeypatch):
    clear_ollama_tags_cache()
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(
        "paperless_agent.ollama_setup.probe_ollama",
        lambda *_a, **_k: {
            "reachable": False,
            "listening": False,
            "base_url": "http://localhost:11434",
            "models": [],
            "version": None,
            "error": "Cannot reach Ollama at http://localhost:11434 — is `ollama serve` running?",
        },
    )
    with pytest.raises(RuntimeError, match="ollama serve"):
        ensure_ollama_ready(force=True)


def test_ensure_ollama_ready_raises_when_models_missing(monkeypatch):
    clear_ollama_tags_cache()
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_NAME", "gemma3")
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setattr(
        "paperless_agent.ollama_setup.probe_ollama",
        lambda *_a, **_k: {
            "reachable": True,
            "listening": True,
            "base_url": "http://localhost:11434",
            "models": ["gemma3:4b"],
            "version": "0.6.0",
            "error": None,
        },
    )
    with pytest.raises(RuntimeError, match="nomic-embed-text"):
        ensure_ollama_ready(force=True, verify_chat=False)


def test_ensure_ollama_ready_verifies_chat_model(monkeypatch):
    clear_ollama_tags_cache()
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(config, "MODEL_NAME", "gemma3")
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setattr(
        "paperless_agent.ollama_setup.probe_ollama",
        lambda *_a, **_k: {
            "reachable": True,
            "listening": True,
            "base_url": "http://localhost:11434",
            "models": ["gemma3:4b", "nomic-embed-text:latest"],
            "version": "0.6.0",
            "error": None,
        },
    )
    monkeypatch.setattr(
        "paperless_agent.ollama_setup.verify_model_accepts_requests",
        lambda model, **_k: None if model == "gemma3:4b" else f"bad:{model}",
    )
    ready = ensure_ollama_ready(force=True)
    assert ready["ready"] is True
    assert ready["listening"] is True
    assert ready["chat_model"] == "gemma3:4b"


def test_model_name_matching():
    assert model_name_matches("gemma3:latest", "gemma3")
    assert model_name_matches("gemma3:4b", "gemma3")
    assert model_name_matches("nomic-embed-text", "nomic-embed-text")
    assert not model_name_matches("llama3.2", "gemma3")


def test_resolve_installed_model_prefers_size_tag():
    assert (
        resolve_installed_model("gemma3", ["gemma3:4b", "nomic-embed-text:latest"]) == "gemma3:4b"
    )
    assert resolve_installed_model("gemma3", ["gemma3:12b", "gemma3:latest"]) == "gemma3:latest"
    assert resolve_installed_model("gemma3", ["llama3.2:latest"]) is None


def test_complete_ollama_uses_resolved_local_tag(ollama_provider, monkeypatch):
    clear_ollama_tags_cache()
    monkeypatch.setattr(
        "paperless_agent.llm.resolve_runtime_model",
        lambda wanted, **_k: "gemma3:4b" if wanted == "gemma3" else wanted,
    )
    monkeypatch.setattr(config, "MODEL_NAME", "gemma3")
    result = asyncio.run(complete_text("hello", instructions="sys"))
    assert result == "transcribed text"
    assert ollama_provider[0]["model"] == "gemma3:4b"


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
    path.chmod(0o644)
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
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600


def test_env_path_defaults_to_project_root(tmp_path, monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv("PAPERLESS_APPIMAGE", raising=False)
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(config, "PROJECT_ROOT", root)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    assert env_path() == root / ".env"


def test_env_path_uses_data_dir_when_appimage(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERLESS_APPIMAGE", "1")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path / "ro")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    assert env_path() == tmp_path / "data" / ".env"


def test_env_path_uses_data_dir_when_project_not_writable(tmp_path, monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv("PAPERLESS_APPIMAGE", raising=False)
    root = tmp_path / "ro"
    root.mkdir()
    monkeypatch.setattr(config, "PROJECT_ROOT", root)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr("paperless_agent.ollama_setup.os.access", lambda *_a, **_k: False)
    assert env_path() == tmp_path / "data" / ".env"


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
        "app.routers.auth.ollama_status",
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


def test_start_ollama_already_running(monkeypatch):
    clear_ollama_tags_cache()
    monkeypatch.setattr(
        "paperless_agent.ollama_setup.probe_ollama",
        lambda *_a, **_k: {
            "reachable": True,
            "listening": True,
            "base_url": "http://localhost:11434",
            "models": ["gemma3:4b", "nomic-embed-text:latest"],
            "version": "0.6.0",
            "error": None,
        },
    )
    monkeypatch.setattr(
        "paperless_agent.ollama_setup.find_ollama_binary",
        lambda: "/usr/bin/ollama",
    )
    result = start_ollama()
    assert result["already_running"] is True
    assert result["started"] is False
    assert result["ollama"]["reachable"] is True


def test_start_ollama_spawns_serve_when_offline(monkeypatch):
    clear_ollama_tags_cache()
    probes = iter(
        [
            {
                "reachable": False,
                "listening": False,
                "base_url": "http://localhost:11434",
                "models": [],
                "version": None,
                "error": "Cannot reach Ollama",
            },
            {
                "reachable": True,
                "listening": True,
                "base_url": "http://localhost:11434",
                "models": ["gemma3:4b", "nomic-embed-text:latest"],
                "version": "0.6.0",
                "error": None,
            },
            {
                "reachable": True,
                "listening": True,
                "base_url": "http://localhost:11434",
                "models": ["gemma3:4b", "nomic-embed-text:latest"],
                "version": "0.6.0",
                "error": None,
            },
        ]
    )
    monkeypatch.setattr(
        "paperless_agent.ollama_setup.probe_ollama",
        lambda *_a, **_k: next(probes),
    )
    monkeypatch.setattr(
        "paperless_agent.ollama_setup.find_ollama_binary",
        lambda: "/usr/bin/ollama",
    )
    monkeypatch.setattr(
        "paperless_agent.ollama_setup._try_systemctl_start",
        lambda: None,
    )
    spawned: list[str] = []
    monkeypatch.setattr(
        "paperless_agent.ollama_setup._spawn_ollama_serve",
        lambda binary: spawned.append(binary),
    )
    result = start_ollama(wait_timeout=2.0)
    assert spawned == ["/usr/bin/ollama"]
    assert result["started"] is True
    assert result["method"] == "/usr/bin/ollama serve"
    assert result["ollama"]["reachable"] is True


def test_start_ollama_requires_binary(monkeypatch):
    clear_ollama_tags_cache()
    monkeypatch.setattr(
        "paperless_agent.ollama_setup.probe_ollama",
        lambda *_a, **_k: {
            "reachable": False,
            "listening": False,
            "base_url": "http://localhost:11434",
            "models": [],
            "version": None,
            "error": "Cannot reach Ollama",
        },
    )
    monkeypatch.setattr(
        "paperless_agent.ollama_setup.find_ollama_binary",
        lambda: None,
    )
    with pytest.raises(RuntimeError, match="not installed"):
        start_ollama(wait_timeout=1.0)


def test_ollama_start_api(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.auth.start_ollama",
        lambda **_k: {
            "status": "success",
            "started": True,
            "already_running": False,
            "method": "systemctl --user start ollama.service",
            "ollama": {
                "reachable": True,
                "listening": True,
                "can_start": False,
                "ready": False,
            },
        },
    )
    resp = client.post("/api/ollama/start")
    assert resp.status_code == 200
    assert resp.json()["started"] is True


def test_infer_model_processor_uses_vram():
    assert infer_model_processor({"size_vram": 0}) == "cpu"
    assert infer_model_processor({"size_vram": 1024}) == "gpu"


def test_summarize_compute_idle_and_gpu():
    idle = summarize_compute([])
    assert idle["compute"] == "idle"
    assert idle["compute_label"] == "idle"

    gpu = summarize_compute([{"name": "gemma3:latest", "size_vram": 2_000_000_000}])
    assert gpu["compute"] == "gpu"
    assert gpu["compute_label"] == "GPU"
    assert gpu["running"][0]["processor"] == "gpu"

    mixed = summarize_compute(
        [
            {"name": "gemma3:latest", "size_vram": 0},
            {"name": "llama3.2:latest", "size_vram": 512},
        ]
    )
    assert mixed["compute"] == "mixed"
    assert mixed["compute_label"] == "CPU + GPU"


def test_ollama_status_includes_compute(monkeypatch):
    clear_ollama_tags_cache()
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(
        "paperless_agent.ollama_setup.probe_ollama",
        lambda *_a, **_k: {
            "reachable": True,
            "listening": True,
            "base_url": "http://localhost:11434",
            "models": ["gemma3:latest"],
            "version": "0.32.9",
            "error": None,
        },
    )
    monkeypatch.setattr(
        "paperless_agent.ollama_setup.fetch_running_ps_models",
        lambda *_a, **_k: [{"name": "gemma3:latest", "size_vram": 0}],
    )
    status = ollama_status()
    assert status["compute"] == "cpu"
    assert status["compute_label"] == "CPU"
    assert status["running_models"][0]["name"] == "gemma3:latest"


def test_llm_busy_detail_is_action_only():
    detail = llm_busy_detail("Extracting metadata")
    assert detail == "Extracting metadata — can take a while"
    assert "Ollama" not in detail
    assert "CPU" not in detail
    assert "gemma3" not in detail


def test_current_compute_label_idle(monkeypatch):
    monkeypatch.setattr(
        "paperless_agent.ollama_setup.fetch_running_ps_models",
        lambda *_a, **_k: [],
    )
    assert current_compute_label() is None
