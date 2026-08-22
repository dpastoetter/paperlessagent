"""Shared fixtures: isolated data directory so tests never touch real data."""

from __future__ import annotations

import os

# Avoid auto-selecting a developer-machine Ollama during provider resolution.
os.environ.setdefault("PAPERLESS_SKIP_OLLAMA_PROBE", "1")
# Keep OCR/parse isolation off in the unit suite by default (spawn + coverage).
# Dedicated tests re-enable PAPERLESS_MEDIA_WORKER=1 explicitly.
os.environ.setdefault("PAPERLESS_MEDIA_WORKER", "0")

import pytest
from fastapi.testclient import TestClient

from app.main import CSRF_HEADER_NAME, CSRF_HEADER_VALUE, app
from paperless_agent.auth_rate_limit import reset_auth_rate_limiter
from paperless_agent.config import ensure_data_dirs
from paperless_agent.privacy import clear_privacy_cache
from paperless_agent.settings import clear_settings_cache, load_settings


@pytest.fixture(autouse=True)
def _reset_auth_rate_limiter():
    reset_auth_rate_limiter()
    yield
    reset_auth_rate_limiter()


@pytest.fixture()
def isolated_data(tmp_path, monkeypatch):
    """Point all storage (settings, DB, inbox, archive) at a temp directory."""
    data = tmp_path / "data"
    monkeypatch.setattr("paperless_agent.config.DATA_DIR", data)
    monkeypatch.setattr("paperless_agent.config.INBOX_DIR", data / "inbox")
    monkeypatch.setattr("paperless_agent.config.ARCHIVE_DIR", data / "archive")
    monkeypatch.setattr("paperless_agent.config.DB_PATH", data / "paperless.db")
    monkeypatch.setattr("paperless_agent.config.CHROMA_DIR", data / "chroma")
    # metadata_db froze DB_PATH at import time; patch its module copy too.
    monkeypatch.setattr("paperless_agent.tools.metadata_db.DB_PATH", data / "paperless.db")
    clear_settings_cache()
    clear_privacy_cache()
    ensure_data_dirs()
    load_settings()
    yield data
    clear_settings_cache()
    clear_privacy_cache()


@pytest.fixture()
def stub_rag_index(monkeypatch):
    """Skip real embedding calls when file_and_persist indexes a document."""
    monkeypatch.setattr(
        "paperless_agent.pipeline.agents.index_document",
        lambda **_kw: {"status": "success", "chunk_count": 1},
    )


@pytest.fixture()
def client(isolated_data):
    """TestClient that always sends the CSRF header required by mutating routes."""
    with TestClient(app) as tc:
        tc.headers.update({CSRF_HEADER_NAME: CSRF_HEADER_VALUE})
        yield tc
