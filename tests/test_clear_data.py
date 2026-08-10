"""Tests for wiping archived files, SQLite, and Chroma."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from paperless_agent.config import ensure_data_dirs
from paperless_agent.settings import clear_settings_cache, load_settings, save_settings
from paperless_agent.tools import metadata_db, rag_index
from paperless_agent.tools.storage import clear_all_stored_data


def test_clear_all_stored_data(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr("paperless_agent.config.DATA_DIR", data)
    monkeypatch.setattr("paperless_agent.config.INBOX_DIR", data / "inbox")
    monkeypatch.setattr("paperless_agent.config.ARCHIVE_DIR", data / "archive")
    monkeypatch.setattr("paperless_agent.config.DB_PATH", data / "paperless.db")
    monkeypatch.setattr("paperless_agent.config.CHROMA_DIR", data / "chroma")
    monkeypatch.setattr("paperless_agent.tools.metadata_db.DB_PATH", data / "paperless.db")
    monkeypatch.setattr("paperless_agent.tools.rag_index.CHROMA_DIR", data / "chroma")
    clear_settings_cache()
    ensure_data_dirs()
    load_settings()

    archive_file = data / "archive" / "invoice" / "2024" / "doc.pdf"
    archive_file.parent.mkdir(parents=True, exist_ok=True)
    archive_file.write_bytes(b"%PDF-1.4 stub")
    inbox_file = data / "inbox" / "scan.pdf"
    inbox_file.write_bytes(b"%PDF-1.4 stub")

    saved = metadata_db.upsert_metadata(
        original_name="scan.pdf",
        filename="doc.pdf",
        path=str(archive_file),
        doc_type="invoice",
        summary="Test invoice",
    )
    doc_id = saved["document_id"]

    def fake_embed(texts):
        return [[0.1] * 8 for _ in texts]

    monkeypatch.setattr(rag_index, "embed_texts", fake_embed)
    indexed = rag_index.index_document(
        document_id=doc_id,
        text="Test invoice from Acme for EUR 120.",
        filename="doc.pdf",
        doc_type="invoice",
    )
    assert indexed["status"] == "success"
    assert (data / "chroma").exists()
    assert (data / "paperless.db").exists()

    # Keep settings around
    settings_before = save_settings(
        {
            "source_dir": str(data / "inbox"),
            "categories": [
                {"name": "invoice", "folder": str(data / "archive" / "invoice")},
                {"name": "other", "folder": str(data / "archive" / "other")},
            ],
            "batch": {"poll_interval_seconds": 30},
        }
    )

    result = clear_all_stored_data()
    assert result["status"] == "success"
    assert not archive_file.exists()
    assert not inbox_file.exists()
    assert metadata_db.list_recent().get("count") == 0
    assert (data / "settings.json").exists()
    assert Path(settings_before["source_dir"]).is_dir()

    # Chroma collection should be empty / recreatable
    hits = rag_index.retrieve_chunks("Acme invoice", top_k=3)
    assert hits["status"] == "success"
    assert hits["count"] == 0
    clear_settings_cache()


def test_clear_all_data_api(tmp_path, monkeypatch):
    monkeypatch.setattr("paperless_agent.config.DATA_DIR", tmp_path)
    monkeypatch.setattr("paperless_agent.config.INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr("paperless_agent.config.ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr("paperless_agent.config.DB_PATH", tmp_path / "paperless.db")
    monkeypatch.setattr("paperless_agent.config.CHROMA_DIR", tmp_path / "chroma")
    monkeypatch.setattr("paperless_agent.tools.metadata_db.DB_PATH", tmp_path / "paperless.db")
    clear_settings_cache()
    (tmp_path / "inbox").mkdir(parents=True)
    load_settings()

    from app.main import CSRF_HEADER_NAME, CSRF_HEADER_VALUE

    client = TestClient(app)
    client.headers.update({CSRF_HEADER_NAME: CSRF_HEADER_VALUE})
    response = client.delete("/api/data")
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    clear_settings_cache()
