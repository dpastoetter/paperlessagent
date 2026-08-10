"""API-level checks for empty inbox processing."""

from fastapi.testclient import TestClient

from app.main import app
from paperless_agent.settings import clear_settings_cache, load_settings


def test_process_inbox_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("paperless_agent.config.DATA_DIR", tmp_path)
    monkeypatch.setattr("paperless_agent.config.INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr("paperless_agent.config.ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr("paperless_agent.config.DB_PATH", tmp_path / "paperless.db")
    monkeypatch.setattr("paperless_agent.config.CHROMA_DIR", tmp_path / "chroma")
    clear_settings_cache()
    (tmp_path / "inbox").mkdir(parents=True)
    load_settings()

    client = TestClient(app)
    response = client.post("/api/process-inbox")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "empty"
    assert payload["processed"] == 0
    assert "empty" in payload["message"].lower()
    clear_settings_cache()
