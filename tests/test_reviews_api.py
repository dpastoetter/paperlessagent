"""API-level tests for the human-in-the-loop review endpoints."""

from __future__ import annotations

from pathlib import Path

from deepcatalog.review import create_review
from deepcatalog.settings import get_source_dir


def _queue_scan(name: str = "scan.pdf", **proposal_overrides) -> tuple[str, str]:
    """Drop a fake scan in the inbox and queue a review for it."""
    inbox = get_source_dir()
    inbox.mkdir(parents=True, exist_ok=True)
    scan = inbox / name
    scan.write_bytes(b"%PDF fake " + name.encode())
    proposal = {
        "filename": f"2024-01-01_Invoice_Acme_{name}",
        "doc_type": "invoice",
        "doc_date": "2024-01-01",
        "summary": "Test invoice",
        "full_text": "test invoice text",
        **proposal_overrides,
    }
    queued = create_review(source_path=str(scan), original_name=name, proposal=proposal)
    return queued["review_id"], str(scan)


def test_reviews_empty(client):
    resp = client.get("/api/reviews")
    assert resp.status_code == 200
    assert resp.json() == {"status": "success", "count": 0, "reviews": []}


def test_reviews_list_and_file_serving(client):
    review_id, _ = _queue_scan()

    listed = client.get("/api/reviews").json()
    assert listed["count"] == 1
    assert listed["reviews"][0]["id"] == review_id
    assert listed["reviews"][0]["proposal"]["doc_type"] == "invoice"

    file_resp = client.get(f"/api/reviews/{review_id}/file")
    assert file_resp.status_code == 200
    assert file_resp.content.startswith(b"%PDF")

    assert client.get("/api/reviews/missing-id/file").status_code == 404


def test_approve_with_overrides_files_document(client, stub_rag_index):
    review_id, scan_path = _queue_scan()

    resp = client.post(
        f"/api/reviews/{review_id}/approve",
        json={"doc_type": "tax", "filename": "2024-01-01_Tax_Acme.pdf"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "2024-01-01_Tax_Acme.pdf"
    assert "/tax/" in body["archive_path"]

    assert not Path(scan_path).exists()
    assert client.get("/api/reviews").json()["count"] == 0

    # Approving the same review again must be rejected.
    assert client.post(f"/api/reviews/{review_id}/approve", json={}).status_code == 409

    # The filed document is visible in the archive listing.
    docs = client.get("/api/documents").json()["documents"]
    assert any(d["filename"] == "2024-01-01_Tax_Acme.pdf" for d in docs)


def test_reject_removes_scan(client):
    review_id, scan_path = _queue_scan("dupe.pdf")

    resp = client.post(f"/api/reviews/{review_id}/reject", json={"delete_file": True})
    assert resp.status_code == 200
    assert resp.json()["file_removed"] is True

    assert not Path(scan_path).exists()
    assert client.get("/api/reviews").json()["count"] == 0

    # Rejecting an unknown review id fails cleanly.
    assert client.post("/api/reviews/nope/reject", json={}).status_code == 409


def test_settings_review_toggle_roundtrip(client):
    settings = client.get("/api/settings").json()["settings"]
    assert settings["review"]["require_approval"] is True

    settings["review"]["require_approval"] = False
    resp = client.put("/api/settings", json=settings)
    assert resp.status_code == 200
    assert resp.json()["settings"]["review"]["require_approval"] is False
