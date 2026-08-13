"""Tests for RAG embedding backends and automatic index rebuild."""

from __future__ import annotations

import json

import pytest

from paperless_agent import config
from paperless_agent.config import ensure_data_dirs
from paperless_agent.settings import clear_settings_cache, load_settings
from paperless_agent.tools import metadata_db, rag_index


@pytest.fixture()
def isolated_data(tmp_path, monkeypatch):
    data = tmp_path / "data"
    chroma = data / "chroma"
    monkeypatch.setattr("paperless_agent.config.DATA_DIR", data)
    monkeypatch.setattr("paperless_agent.config.INBOX_DIR", data / "inbox")
    monkeypatch.setattr("paperless_agent.config.ARCHIVE_DIR", data / "archive")
    monkeypatch.setattr("paperless_agent.config.DB_PATH", data / "paperless.db")
    monkeypatch.setattr("paperless_agent.config.CHROMA_DIR", chroma)
    monkeypatch.setattr("paperless_agent.tools.metadata_db.DB_PATH", data / "paperless.db")
    monkeypatch.setattr("paperless_agent.tools.rag_index.CHROMA_DIR", chroma)
    clear_settings_cache()
    ensure_data_dirs()
    load_settings()
    yield data
    clear_settings_cache()


def _fake_embed_factory(dims: int = 8):
    def fake_embed(texts):
        vectors = []
        for t in texts:
            vec = [0.0] * dims
            for i, ch in enumerate((t or "").encode("utf-8")):
                vec[i % dims] += (ch % 17) / 17.0
            vectors.append(vec)
        return vectors

    return fake_embed


def test_no_byte_hash_local_embedder():
    assert not hasattr(rag_index, "_embed_local")
    assert callable(rag_index._embed_local_semantic)


def test_local_semantic_uses_onnx(monkeypatch):
    calls: list[list[str]] = []

    def fake_onnx(texts):
        calls.append(list(texts))
        return [[0.1, 0.2, 0.3] for _ in texts]

    monkeypatch.setattr(rag_index, "_embed_onnx", fake_onnx)
    out = rag_index._embed_local_semantic(["alpha", "beta"])
    assert calls == [["alpha", "beta"]]
    assert out == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]


def test_index_meta_written_and_stale_rebuild(isolated_data, monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr("paperless_agent.auth.resolve_auth_mode", lambda: "chatgpt_oauth")
    monkeypatch.setattr("paperless_agent.auth.resolve_openai_api_key", lambda: None)
    monkeypatch.setattr(rag_index, "embed_texts", _fake_embed_factory(8))

    archive_path = isolated_data / "archive" / "doc.pdf"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_bytes(b"%PDF")
    saved = metadata_db.upsert_metadata(
        original_name="scan.pdf",
        filename="doc.pdf",
        path=str(archive_path),
        doc_type="invoice",
        summary="Invoice from Acme for consulting.",
        extracted_json=json.dumps({"full_text": "Invoice from Acme for consulting."}),
    )
    doc_id = saved["document_id"]

    indexed = rag_index.index_document(
        document_id=doc_id,
        text="Invoice from Acme for consulting.",
        filename="doc.pdf",
        doc_type="invoice",
    )
    assert indexed["status"] == "success"

    meta = rag_index._load_index_meta()
    assert meta is not None
    assert meta["embedding_provider"] == "local-onnx"
    assert meta["model"] == rag_index.ONNX_MODEL_NAME
    assert meta["schema_version"] == rag_index.INDEX_SCHEMA_VERSION
    assert meta["dimension"] == 8
    assert meta.get("stale") is False

    # Force a fingerprint mismatch → automatic rebuild on next retrieve.
    rebuilds: list[str] = []
    real_rebuild = rag_index.rebuild_index

    def tracking_rebuild(*, reason: str = ""):
        rebuilds.append(reason)
        return real_rebuild(reason=reason)

    monkeypatch.setattr(rag_index, "rebuild_index", tracking_rebuild)
    rag_index.mark_index_stale("unit-test")
    hits = rag_index.retrieve_chunks("Acme consulting invoice", top_k=3)
    assert hits["status"] == "success"
    assert rebuilds
    assert rag_index._load_index_meta()["stale"] is False
