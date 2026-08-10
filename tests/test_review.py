"""Tests for duplicate detection and the human-in-the-loop review queue."""

from __future__ import annotations

import pytest

from paperless_agent.config import ensure_data_dirs
from paperless_agent.dedup import (
    content_hash,
    file_checksum,
    find_duplicates,
    text_similarity,
)
from paperless_agent.review import (
    approve_review,
    create_review,
    list_pending,
    pending_source_paths,
    reject_review,
)
from paperless_agent.settings import clear_settings_cache, load_settings
from paperless_agent.tools.metadata_db import upsert_metadata


@pytest.fixture()
def isolated_data(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr("paperless_agent.config.DATA_DIR", data)
    monkeypatch.setattr("paperless_agent.config.INBOX_DIR", data / "inbox")
    monkeypatch.setattr("paperless_agent.config.ARCHIVE_DIR", data / "archive")
    monkeypatch.setattr("paperless_agent.config.DB_PATH", data / "paperless.db")
    monkeypatch.setattr("paperless_agent.config.CHROMA_DIR", data / "chroma")
    # metadata_db froze DB_PATH at import time; patch its module-level copy too.
    monkeypatch.setattr(
        "paperless_agent.tools.metadata_db.DB_PATH", data / "paperless.db"
    )
    clear_settings_cache()
    ensure_data_dirs()
    load_settings()
    yield data
    clear_settings_cache()


def _stub_index(monkeypatch):
    monkeypatch.setattr(
        "paperless_agent.pipeline.agents.index_document",
        lambda **_kw: {"status": "success", "chunk_count": 1},
    )


def test_checksum_and_content_hash(tmp_path):
    f1 = tmp_path / "a.pdf"
    f2 = tmp_path / "b.pdf"
    f1.write_bytes(b"same bytes")
    f2.write_bytes(b"same bytes")
    assert file_checksum(f1) == file_checksum(f2)

    assert content_hash("Invoice  #42 from ACME") == content_hash(
        "invoice #42 FROM acme\n"
    )
    assert content_hash("") is None


def test_text_similarity():
    a = "invoice from acme corp for services rendered in march total 120 euro"
    b = "invoice from acme corp for services rendered in april total 120 euro"
    assert text_similarity(a, b) > 0.8
    assert text_similarity(a, "completely unrelated medical report") < 0.2


def test_find_duplicates_exact_and_content(isolated_data):
    upsert_metadata(
        original_name="scan.pdf",
        filename="2024-01-01_Invoice_Acme.pdf",
        path=str(isolated_data / "archive" / "invoice.pdf"),
        checksum="abc123",
        content_hash=content_hash("hello world invoice"),
    )

    exact = find_duplicates("abc123", None)
    assert exact and exact[0]["kind"] == "exact"

    by_content = find_duplicates("otherchecksum", "Hello, WORLD invoice!")
    assert by_content and by_content[0]["kind"] == "content"

    assert find_duplicates("nomatch", "entirely different text here") == []


def test_review_queue_and_approve(isolated_data, monkeypatch):
    _stub_index(monkeypatch)
    inbox = isolated_data / "inbox"
    scan = inbox / "scan.pdf"
    scan.write_bytes(b"%PDF fake")

    queued = create_review(
        source_path=str(scan),
        original_name="scan.pdf",
        proposal={
            "filename": "2024-03-15_Invoice_Acme.pdf",
            "doc_type": "invoice",
            "doc_date": "2024-03-15",
            "summary": "Acme invoice",
            "full_text": "acme invoice text",
        },
        checksum=file_checksum(scan),
        content_hash=content_hash("acme invoice text"),
    )
    review_id = queued["review_id"]

    pending = list_pending()
    assert pending["count"] == 1
    assert str(scan) in pending_source_paths()

    # Human corrects the category before approving.
    result = approve_review(review_id, {"doc_type": "tax"})
    assert result["status"] in {"success", "partial"}
    assert not scan.exists()  # moved out of the inbox
    assert "tax" in result["archive_path"]
    assert result["metadata"]["checksum"] == queued["review"]["checksum"]

    assert list_pending()["count"] == 0
    # Approving twice must fail.
    assert approve_review(review_id)["status"] == "error"


def test_reject_removes_inbox_file_only(isolated_data):
    inbox = isolated_data / "inbox"
    scan = inbox / "dupe.pdf"
    scan.write_bytes(b"bytes")

    queued = create_review(
        source_path=str(scan),
        original_name="dupe.pdf",
        proposal={"filename": "dupe.pdf", "doc_type": "other"},
        duplicates=[{"kind": "exact", "document_id": "x", "filename": "old.pdf"}],
    )
    review = list_pending()["reviews"][0]
    assert review["duplicates"][0]["kind"] == "exact"

    result = reject_review(queued["review_id"], delete_file=True)
    assert result["status"] == "success"
    assert result["file_removed"] is True
    assert not scan.exists()
    assert list_pending()["count"] == 0


def test_reject_refuses_files_outside_inbox(isolated_data, tmp_path):
    outside = tmp_path / "elsewhere.pdf"
    outside.write_bytes(b"keep me")

    queued = create_review(
        source_path=str(outside),
        original_name="elsewhere.pdf",
        proposal={"filename": "elsewhere.pdf", "doc_type": "other"},
    )
    result = reject_review(queued["review_id"], delete_file=True)
    assert result["status"] == "success"
    assert result["file_removed"] is False
    assert outside.exists()
