"""Unit tests for filesystem, metadata, and RAG helpers (no live LLM required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from paperless_agent.config import ensure_data_dirs
from paperless_agent.settings import clear_settings_cache, load_settings
from paperless_agent.tools import filesystem, metadata_db, rag_index


@pytest.fixture()
def isolated_data(tmp_path, monkeypatch):
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
    yield data
    clear_settings_cache()


def _write_minimal_pdf(path: Path, text: str = "Invoice Acme EUR 120") -> None:
    # Minimal valid-enough PDF with a text stream for pypdf
    content = f"BT /F1 12 Tf 100 700 Td ({text}) Tj ET"
    stream = content.encode("latin-1", errors="replace")
    objects = []
    objects.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
    objects.append(b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n")
    objects.append(
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj\n"
    )
    objects.append(
        f"4 0 obj<< /Length {len(stream)} >>stream\n".encode() + stream + b"\nendstream\nendobj\n"
    )
    objects.append(b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n")
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj)
    xref_pos = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode())
    pdf.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        pdf.extend(f"{off:010d} 00000 n \n".encode())
    pdf.extend(
        f"trailer<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    )
    path.write_bytes(pdf)


def test_propose_filename():
    result = filesystem.propose_filename(
        doc_type="invoice",
        doc_date="2024-03-15",
        counterparty="Acme GmbH",
        amount=120,
        currency="EUR",
    )
    assert result["status"] == "success"
    assert result["filename"] == "2024-03-15_Invoice_Acme_GmbH_EUR120.pdf"


def test_propose_filename_medical_with_subject():
    result = filesystem.propose_filename(
        doc_type="medical",
        doc_date="2024-06-12",
        subject="Blood test results",
        counterparty="Dr. Weber",
    )
    assert result["status"] == "success"
    assert result["filename"] == "2024-06-12_Medical_Blood_test_results_Dr._Weber.pdf"


def test_propose_filename_letter_without_amount():
    result = filesystem.propose_filename(
        doc_type="letter",
        doc_date=None,
        subject="Rent increase notice",
        counterparty="Landlord",
    )
    assert result["status"] == "success"
    assert result["filename"] == "undated_Letter_Rent_increase_notice_Landlord.pdf"
    assert "EUR" not in result["filename"]


def test_search_metadata_includes_subject(isolated_data):
    saved = metadata_db.upsert_metadata(
        original_name="scan.pdf",
        filename="2024-01-01_Letter_School-enrollment.pdf",
        path=str(isolated_data / "archive" / "letter" / "2024" / "doc.pdf"),
        doc_type="letter",
        doc_date="2024-01-01",
        subject="School enrollment confirmation",
        counterparties="Grundschule Nord",
        summary="Enrollment letter for the 2024 school year.",
    )
    found = metadata_db.search_metadata(query="enrollment")
    assert found["count"] >= 1
    assert any(d["id"] == saved["document_id"] for d in found["documents"])


def test_search_metadata_fts_matches_tokens_in_question(isolated_data):
    saved = metadata_db.upsert_metadata(
        original_name="scan.pdf",
        filename="2022-09-05_Invoice_BV_CRE8_EUR181p50.pdf",
        path=str(isolated_data / "archive" / "invoice" / "2022" / "doc.pdf"),
        doc_type="invoice",
        doc_date="2022-09-05",
        counterparties="BV CRE8",
        summary="Invoice FA2022-0001 from BV CRE8 for €181.50.",
        extracted_json='{"reference_ids":["FA2022-0001"]}',
    )
    found = metadata_db.search_metadata(
        query="What is the amount on invoice FA2022-0001 from CRE8?"
    )
    assert found["search"] == "fts"
    assert found["count"] >= 1
    assert any(d["id"] == saved["document_id"] for d in found["documents"])


def test_build_fts_match_query_drops_stopwords():
    match = metadata_db.build_fts_match_query("What invoices do I have from Acme for FA2022-0001?")
    assert match is not None
    assert "invoices" in match.lower() or "invoice" in match.lower()
    assert "Acme" in match or "acme" in match.lower()
    assert "FA2022-0001" in match
    assert '"what"' not in match.lower()
    assert '"have"' not in match.lower()


def test_move_and_metadata(isolated_data):
    src = isolated_data / "inbox" / "scan.pdf"
    _write_minimal_pdf(src)
    named = filesystem.propose_filename(
        doc_type="invoice",
        doc_date="2024-03-15",
        counterparty="Acme",
        amount=120,
        original_path=str(src),
    )
    moved = filesystem.move_to_archive(
        source_path=str(src),
        filename=named["filename"],
        doc_type="invoice",
        year="2024",
    )
    assert moved["status"] == "success"
    assert Path(moved["archive_path"]).exists()
    assert "invoice/2024" in moved["archive_path"]

    saved = metadata_db.upsert_metadata(
        original_name="scan.pdf",
        filename=moved["filename"],
        path=moved["archive_path"],
        doc_type="invoice",
        doc_date="2024-03-15",
        counterparties="Acme",
        amount=120,
        currency="EUR",
        summary="Test invoice from Acme for EUR 120.",
    )
    assert saved["status"] == "success"
    doc_id = saved["document_id"]

    found = metadata_db.search_metadata(query="Acme", doc_type="invoice")
    assert found["count"] >= 1
    assert any(d["id"] == doc_id for d in found["documents"])

    got = metadata_db.get_document(doc_id)
    assert got["status"] == "success"
    assert got["document"]["filename"] == moved["filename"]


def test_chunk_text_overlap():
    text = ("Paragraph one about invoices.\n\n" * 40) + ("More details on payments.\n\n" * 40)
    chunks = rag_index.chunk_text(text, chunk_size=200, overlap=40)
    assert len(chunks) > 1
    assert all(chunks)


def test_index_and_retrieve_with_stub_embeddings(isolated_data, monkeypatch):
    archive_path = isolated_data / "archive" / "invoice" / "2024" / "doc.pdf"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_bytes(b"%PDF-1.4 stub")
    saved = metadata_db.upsert_metadata(
        original_name="scan.pdf",
        filename="2024-03-15_Invoice_Acme_EUR120.pdf",
        path=str(archive_path),
        doc_type="invoice",
        doc_date="2024-03-15",
        counterparties="Acme",
        summary="Invoice from Acme for office supplies totaling 120 EUR.",
        extracted_json='{"ids":["INV-1"]}',
    )
    doc_id = saved["document_id"]

    def fake_embed(texts):
        # Deterministic tiny vectors: bag-of-ascii hash into 8 dims
        vectors = []
        for t in texts:
            vec = [0.0] * 8
            for i, ch in enumerate(t.encode("utf-8")):
                vec[i % 8] += (ch % 17) / 17.0
            vectors.append(vec)
        return vectors

    monkeypatch.setattr(rag_index, "embed_texts", fake_embed)

    indexed = rag_index.index_document(
        document_id=doc_id,
        text="Invoice from Acme for office supplies totaling 120 EUR. INV-1 due soon.",
        filename="2024-03-15_Invoice_Acme_EUR120.pdf",
        doc_type="invoice",
    )
    assert indexed["status"] == "success"
    assert indexed["chunk_count"] >= 1

    hits = rag_index.retrieve_chunks("Acme invoice office supplies", top_k=3)
    assert hits["status"] == "success"
    assert hits["count"] >= 1
    assert hits["chunks"][0]["document_id"] == doc_id
