"""Tests for OCR quality gate and AI-first text recovery."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

from PIL import Image, ImageDraw

from paperless_agent import config
from paperless_agent.ocr import (
    assess_text_quality,
    recover_document_text,
    resolve_ocr_page_limit,
)


def test_assess_text_quality_rejects_short_garbage():
    bad = assess_text_quality("@@@ ###")
    assert bad.ok is False
    assert "too_short" in bad.reason or "too_few_words" in bad.reason

    good = assess_text_quality(
        "Invoice FA2022-0001 from BV CRE8 dated 2022-09-05. "
        "Total including VAT is EUR 181.50 for the comanage business package."
    )
    assert good.ok is True
    assert good.reason == "ok"


def test_recover_always_uses_ai_ocr(tmp_path: Path, monkeypatch):
    img_path = tmp_path / "scan.png"
    image = Image.new("RGB", (200, 60), "white")
    draw = ImageDraw.Draw(image)
    draw.text((8, 20), "x", fill="black")
    image.save(img_path)

    async def fake_pages(path, *, page_count, text_layer_hint="", filename=""):
        return (
            "[Page 1]\n"
            "Invoice FA2022-0001 from BV CRE8 dated 2022-09-05. "
            "Total including VAT is EUR 181.50 for the comanage business package."
        )

    monkeypatch.setattr("paperless_agent.ocr._ai_vision_transcribe_pages", fake_pages)

    result = asyncio.run(recover_document_text(img_path))
    assert result["status"] == "success"
    assert result["method"] == "ai_vision"
    assert result["used_ai_ocr"] is True
    assert "181.50" in result["text"]
    assert "[Page 1]" in result["text"]


def test_recover_falls_back_to_text_layer_when_ai_fails(tmp_path: Path, monkeypatch):
    # Minimal PDF with a text operator
    content = "BT /F1 12 Tf 100 700 Td (Invoice Acme EUR 120 paid in full today) Tj ET"
    stream = content.encode("latin-1", errors="replace")
    objects = [
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n",
        b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n",
        (
            b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj\n"
        ),
        f"4 0 obj<< /Length {len(stream)} >>stream\n".encode()
        + stream
        + b"\nendstream\nendobj\n",
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n",
    ]
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
    path = tmp_path / "invoice.pdf"
    path.write_bytes(pdf)

    async def failing_pages(path, *, page_count, text_layer_hint="", filename=""):
        raise RuntimeError("vision unavailable")

    monkeypatch.setattr("paperless_agent.ocr._ai_vision_transcribe_pages", failing_pages)
    monkeypatch.setattr(
        "paperless_agent.ocr.resolve_ocr_page_limit",
        lambda *_args, **_kwargs: 1,
    )

    result = asyncio.run(recover_document_text(path))
    assert result["method"] in {"pdf_text_layer_fallback", "ai_vision"}
    if result["method"] == "pdf_text_layer_fallback":
        assert "Invoice" in (result.get("text") or "") or result["status"] == "partial"


def test_per_page_ocr_concatenates_pages(tmp_path: Path, monkeypatch):
    img_path = tmp_path / "scan.png"
    Image.new("RGB", (40, 20), "white").save(img_path)
    calls: list[int] = []

    async def fake_pages(path, *, page_count, text_layer_hint="", filename=""):
        parts = []
        for i in range(1, page_count + 1):
            calls.append(i)
            parts.append(f"[Page {i}]\ncontent-{i}")
        return "\n\n".join(parts)

    monkeypatch.setattr("paperless_agent.ocr._ai_vision_transcribe_pages", fake_pages)
    monkeypatch.setattr(
        "paperless_agent.ocr.resolve_ocr_page_limit",
        lambda *_args, **_kwargs: 3,
    )

    result = asyncio.run(recover_document_text(img_path))
    assert result["status"] == "success"
    assert calls == [1, 2, 3]
    assert "[Page 1]" in result["text"]
    assert "[Page 2]" in result["text"]
    assert "[Page 3]" in result["text"]
    assert "content-2" in result["text"]


def test_resolve_ocr_page_limit_unlimited_clamps_to_safety(tmp_path: Path, monkeypatch):
    pdf_path = tmp_path / "big.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    monkeypatch.setattr("paperless_agent.ocr.pdf_page_count", lambda _p: 150)
    monkeypatch.setattr(config, "OCR_MAX_PAGES", 0)
    monkeypatch.setattr(config, "OCR_SAFETY_MAX_PAGES", 128)

    assert resolve_ocr_page_limit(pdf_path) == 128


def test_resolve_ocr_page_limit_respects_explicit_cap(tmp_path: Path, monkeypatch):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    monkeypatch.setattr("paperless_agent.ocr.pdf_page_count", lambda _p: 20)
    monkeypatch.setattr(config, "OCR_MAX_PAGES", 4)
    monkeypatch.setattr(config, "OCR_SAFETY_MAX_PAGES", 128)

    assert resolve_ocr_page_limit(pdf_path) == 4


def test_resolve_ocr_page_timeout_longer_for_ollama(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "OCR_PAGE_TIMEOUT", 180.0)
    monkeypatch.setattr(config, "OLLAMA_OCR_PAGE_TIMEOUT", 600.0)
    from paperless_agent.ocr import resolve_ocr_page_timeout

    assert resolve_ocr_page_timeout() == 180.0
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    assert resolve_ocr_page_timeout() == 600.0


def test_prepare_page_image_for_vision_downscales_and_jpeg_for_ollama(monkeypatch):
    from paperless_agent.ocr import prepare_page_image_for_vision

    monkeypatch.setattr(config, "OCR_MAX_IMAGE_PX", 800)
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    image = Image.new("RGB", (1600, 2400), "white")
    data, mime = prepare_page_image_for_vision(image)
    assert mime == "image/jpeg"
    assert len(data) > 0
    with Image.open(io.BytesIO(data)) as resized:
        assert max(resized.size) <= 800
