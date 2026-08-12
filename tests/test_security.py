"""Production hardening: CSRF, path confinement, upload limits, atomic filing."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import CSRF_HEADER_NAME, CSRF_HEADER_VALUE, app
from paperless_agent.pipeline.agents import file_and_persist
from paperless_agent.review import create_review
from paperless_agent.settings import get_source_dir
from paperless_agent.tools.metadata_db import get_document, list_recent


def test_mutating_routes_require_csrf_header(isolated_data):
    bare = TestClient(app)
    # Body-less POSTs are the classic CSRF vector for local unauthenticated APIs.
    for path in (
        "/api/process-inbox",
        "/api/update/apply",
        "/api/update/restart",
        "/api/auth/logout",
        "/api/ollama/enable",
        "/api/ollama/start",
        "/api/autostart",
        "/api/llm/provider",
        "/api/privacy/cloud-disclaimer",
    ):
        resp = bare.post(path)
        assert resp.status_code == 403, path
        assert "cross-site" in resp.json()["detail"]

    # GET remains open (no state change).
    assert bare.get("/api/health").status_code == 200


def test_csrf_header_allows_mutation(client):
    resp = client.post("/api/process-inbox")
    assert resp.status_code == 200
    assert resp.json()["status"] in {"empty", "success", "partial"}


def test_process_rejects_paths_outside_inbox(client, isolated_data):
    secret = isolated_data.parent / "secret.txt"
    secret.write_text("credentials")
    resp = client.post("/api/process", json={"path": str(secret)})
    assert resp.status_code == 400
    assert "inbox" in resp.json()["detail"].lower()


def test_review_file_rejects_outside_inbox(client, isolated_data):
    secret = isolated_data.parent / "outside.pdf"
    secret.write_bytes(b"%PDF outside")
    queued = create_review(
        source_path=str(secret),
        original_name="outside.pdf",
        proposal={"filename": "x.pdf", "doc_type": "other"},
    )
    resp = client.get(f"/api/reviews/{queued['review_id']}/file")
    assert resp.status_code == 403


def test_upload_rejects_unsupported_type(client):
    resp = client.post(
        "/api/upload",
        files={"file": ("malware.exe", b"MZ fake", "application/octet-stream")},
    )
    assert resp.status_code == 415


def test_upload_accepts_pdf(client):
    resp = client.post(
        "/api/upload",
        files={"file": ("scan.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 200
    assert resp.json()["filename"] == "scan.pdf"
    assert (get_source_dir() / "scan.pdf").exists()


def test_file_and_persist_is_atomic_on_metadata_failure(
    isolated_data, stub_rag_index, monkeypatch
):
    inbox = get_source_dir()
    scan = inbox / "keep_me.pdf"
    scan.write_bytes(b"%PDF keep")

    def boom(**_kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "paperless_agent.pipeline.agents.upsert_metadata", boom
    )
    result = file_and_persist(
        source_path=str(scan),
        filename="2024-01-01_Invoice_Test.pdf",
        doc_type="invoice",
        doc_date="2024-01-01",
        summary="test",
    )
    assert result["status"] == "error"
    # Source must still be in the inbox; no orphan archive copy.
    assert scan.exists()
    assert list_recent(limit=10)["count"] == 0


def test_approve_confines_source_to_inbox(client, isolated_data, stub_rag_index):
    outside = isolated_data.parent / "escape.pdf"
    outside.write_bytes(b"%PDF escape")
    queued = create_review(
        source_path=str(outside),
        original_name="escape.pdf",
        proposal={
            "filename": "2024-01-01_Other_Escape.pdf",
            "doc_type": "other",
            "summary": "nope",
        },
    )
    resp = client.post(f"/api/reviews/{queued['review_id']}/approve", json={})
    assert resp.status_code == 409
    assert outside.exists()
    assert get_document(queued["review_id"]).get("status") != "success"
