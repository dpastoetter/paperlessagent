"""Documents, retrieve, ask, and reveal API coverage."""

from __future__ import annotations

from paperless_agent.tools import metadata_db, rag_index


def _seed_document(isolated_data, monkeypatch):
    archive = isolated_data / "archive" / "invoice" / "2022" / "doc.pdf"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"%PDF-1.4 filed")

    def fake_embed(texts):
        vectors = []
        for t in texts:
            vec = [0.0] * 8
            for i, ch in enumerate(t.encode("utf-8")):
                vec[i % 8] += (ch % 17) / 17.0
            vectors.append(vec)
        return vectors

    monkeypatch.setattr(rag_index, "embed_texts", fake_embed)
    saved = metadata_db.upsert_metadata(
        original_name="scan.pdf",
        filename="2022-09-05_Invoice_Acme_EUR10.pdf",
        path=str(archive),
        doc_type="invoice",
        doc_date="2022-09-05",
        counterparties="Acme",
        amount=10,
        currency="EUR",
        summary="Invoice FA-1 from Acme for EUR 10.",
    )
    doc_id = saved["document_id"]
    rag_index.index_document(
        document_id=doc_id,
        text="Invoice FA-1 from Acme dated 2022-09-05. Total EUR 10.",
        filename="2022-09-05_Invoice_Acme_EUR10.pdf",
        doc_type="invoice",
    )
    return doc_id, archive


def test_documents_list_and_search(client, isolated_data, monkeypatch):
    doc_id, _archive = _seed_document(isolated_data, monkeypatch)
    listed = client.get("/api/documents")
    assert listed.status_code == 200
    assert listed.json()["count"] >= 1

    found = client.get("/api/documents", params={"q": "Acme", "doc_type": "invoice"})
    assert found.status_code == 200
    assert any(d["id"] == doc_id for d in found.json()["documents"])

    one = client.get(f"/api/documents/{doc_id}")
    assert one.status_code == 200
    assert one.json()["document"]["id"] == doc_id


def test_document_file_and_path_confinement(client, isolated_data, monkeypatch):
    doc_id, archive = _seed_document(isolated_data, monkeypatch)
    ok = client.get(f"/api/documents/{doc_id}/file")
    assert ok.status_code == 200
    assert ok.headers.get("content-type", "").startswith("application/pdf")

    # Point metadata at a path outside archive roots → 403.
    escape = isolated_data.parent / "secret.pdf"
    escape.write_bytes(b"%PDF secret")
    metadata_db.upsert_metadata(
        document_id=doc_id,
        original_name="scan.pdf",
        filename="escape.pdf",
        path=str(escape),
        doc_type="other",
    )
    denied = client.get(f"/api/documents/{doc_id}/file")
    assert denied.status_code == 403


def test_document_reveal_mocked(client, isolated_data, monkeypatch):
    doc_id, _archive = _seed_document(isolated_data, monkeypatch)
    monkeypatch.setattr(
        "app.routers.documents.reveal_in_explorer",
        lambda path: {"status": "success", "path": path, "opened": "explorer"},
    )
    resp = client.post(f"/api/documents/{doc_id}/reveal")
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


def test_retrieve_and_ask(client, isolated_data, monkeypatch):
    _seed_document(isolated_data, monkeypatch)

    hits = client.get("/api/retrieve", params={"q": "Acme invoice"})
    assert hits.status_code == 200
    assert hits.json()["status"] == "success"

    async def fake_ask(question: str):
        return {
            "status": "success",
            "reply": f"Answer to: {question}",
            "sources": [],
            "grounded": True,
            "retrieval_count": 1,
            "metadata_count": 0,
        }

    monkeypatch.setattr("app.routers.documents.run_query", fake_ask)
    asked = client.post("/api/ask", json={"question": "How much did Acme charge?"})
    assert asked.status_code == 200
    assert "Acme" in asked.json()["reply"] or "Answer" in asked.json()["reply"]


def test_ask_rejects_empty_question(client):
    resp = client.post("/api/ask", json={"question": ""})
    assert resp.status_code == 422
