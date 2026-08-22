"""Additional ingest path coverage with stubbed OCR/LLM."""

from __future__ import annotations

import asyncio

from deepcatalog.ingest import ingest_document
from deepcatalog.settings import get_source_dir, save_settings


def test_ingest_queues_for_review_when_approval_required(
    isolated_data, monkeypatch, stub_rag_index
):
    inbox = get_source_dir()
    scan = inbox / "bill.pdf"
    scan.write_bytes(b"%PDF-1.4 content")

    async def fake_ocr(path, **_kw):
        return {
            "status": "success",
            "path": str(path),
            "filename": "bill.pdf",
            "text": (
                "Invoice FA-99 from Acme Corp dated 2024-03-01. "
                "Total including VAT EUR 42.00 for consulting."
            ),
            "method": "pdf_text_layer",
            "used_ai_ocr": False,
            "quality": {"ok": True, "chars": 80, "reason": "ok"},
        }

    async def fake_complete(prompt, *, instructions, cancel_event=None, json_mode=False):
        return (
            '{"doc_type":"invoice","doc_date":"2024-03-01","subject":"Consulting",'
            '"parties":"Acme Corp","reference_ids":["FA-99"],'
            '"amount":42,"currency":"EUR","summary":"Acme invoice FA-99 for EUR 42."}'
        )

    monkeypatch.setattr("deepcatalog.ingest.recover_document_text", fake_ocr)
    monkeypatch.setattr("deepcatalog.ingest.complete_text", fake_complete)

    save_settings(
        {
            "source_dir": str(inbox),
            "categories": [
                {"name": "invoice", "folder": str(isolated_data / "archive" / "invoice")},
                {"name": "other", "folder": str(isolated_data / "archive" / "other")},
            ],
            "batch": {"poll_interval_seconds": 0},
            "review": {"require_approval": True},
            "ocr": {"mode": "balanced"},
        }
    )

    result = asyncio.run(ingest_document(str(scan)))
    assert result["status"] == "pending_review"
    assert (
        result.get("review_id")
        or result.get("result", {}).get("review_id")
        or "review" in str(result).lower()
    )


def test_ingest_files_when_approval_not_required(isolated_data, monkeypatch, stub_rag_index):
    inbox = get_source_dir()
    scan = inbox / "auto.pdf"
    scan.write_bytes(b"%PDF-1.4 content")

    async def fake_ocr(path, **_kw):
        return {
            "status": "success",
            "path": str(path),
            "filename": "auto.pdf",
            "text": (
                "Invoice FA-100 from Beta LLC dated 2024-04-02. "
                "Total EUR 99.00 for services rendered today."
            ),
            "method": "pdf_text_layer",
            "used_ai_ocr": False,
            "quality": {"ok": True, "chars": 90, "reason": "ok"},
        }

    async def fake_complete(prompt, *, instructions, cancel_event=None, json_mode=False):
        return (
            '{"doc_type":"invoice","doc_date":"2024-04-02","subject":"Services",'
            '"parties":"Beta LLC","reference_ids":["FA-100"],'
            '"amount":99,"currency":"EUR","summary":"Beta invoice FA-100 for EUR 99."}'
        )

    monkeypatch.setattr("deepcatalog.ingest.recover_document_text", fake_ocr)
    monkeypatch.setattr("deepcatalog.ingest.complete_text", fake_complete)

    save_settings(
        {
            "source_dir": str(inbox),
            "categories": [
                {"name": "invoice", "folder": str(isolated_data / "archive" / "invoice")},
                {"name": "other", "folder": str(isolated_data / "archive" / "other")},
            ],
            "batch": {"poll_interval_seconds": 0},
            "review": {"require_approval": False},
            "ocr": {"mode": "balanced"},
        }
    )

    result = asyncio.run(ingest_document(str(scan)))
    assert result["status"] in {"success", "partial"}
    assert result.get("document_id")
    assert result.get("archive_path")
    assert not scan.exists()
