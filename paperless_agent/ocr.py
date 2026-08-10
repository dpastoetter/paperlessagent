"""Document text recovery: optional PDF text layer, then always AI vision OCR."""

from __future__ import annotations

import base64
import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image
from pypdf import PdfReader

from paperless_agent.progress import emit_step
from paperless_agent.tools.filesystem import IMAGE_SUFFIXES, PDF_SUFFIXES, SUPPORTED_SUFFIXES

logger = logging.getLogger(__name__)

MAX_OCR_PAGES = 4
OCR_DPI = 200
MIN_CHARS = 40
MIN_WORDS = 8
MIN_ALNUM_RATIO = 0.45
MIN_AVG_CONFIDENCE = 0.45


@dataclass
class TextQuality:
    ok: bool
    chars: int
    words: int
    alnum_ratio: float
    avg_confidence: float | None
    reason: str


def assess_text_quality(
    text: str,
    confidences: list[float] | None = None,
) -> TextQuality:
    """Heuristic quality summary for recovered text."""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    chars = len(cleaned)
    words = len(cleaned.split()) if cleaned else 0
    alnum = sum(1 for ch in cleaned if ch.isalnum())
    alnum_ratio = alnum / max(chars, 1)
    avg_conf: float | None = None
    if confidences:
        usable = [float(c) for c in confidences if c is not None]
        if usable:
            avg_conf = sum(usable) / len(usable)

    reasons: list[str] = []
    if chars < MIN_CHARS:
        reasons.append(f"too_short<{MIN_CHARS}")
    if words < MIN_WORDS:
        reasons.append(f"too_few_words<{MIN_WORDS}")
    if alnum_ratio < MIN_ALNUM_RATIO:
        reasons.append(f"low_alnum<{MIN_ALNUM_RATIO}")
    if avg_conf is not None and avg_conf < MIN_AVG_CONFIDENCE:
        reasons.append(f"low_confidence<{MIN_AVG_CONFIDENCE}")

    ok = not reasons
    return TextQuality(
        ok=ok,
        chars=chars,
        words=words,
        alnum_ratio=round(alnum_ratio, 3),
        avg_confidence=None if avg_conf is None else round(avg_conf, 3),
        reason="ok" if ok else ",".join(reasons),
    )


def _extract_pdf_text_layer(path: Path) -> tuple[str, int | None]:
    reader = PdfReader(str(path))
    pages: list[str] = []
    for i, page in enumerate(reader.pages):
        page_text = (page.extract_text() or "").strip()
        if page_text:
            pages.append(f"[Page {i + 1}]\n{page_text}")
    return "\n\n".join(pages).strip(), len(reader.pages)


def render_document_images(
    path: Path,
    *,
    max_pages: int = MAX_OCR_PAGES,
    dpi: int = OCR_DPI,
) -> list[Image.Image]:
    """Rasterize a PDF or load an image for vision OCR."""
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        with Image.open(path) as img:
            return [img.convert("RGB")]

    if suffix not in PDF_SUFFIXES:
        return []

    from pdf2image import convert_from_path

    images = convert_from_path(
        str(path),
        dpi=dpi,
        first_page=1,
        last_page=max_pages,
        fmt="png",
    )
    return [img.convert("RGB") if img.mode != "RGB" else img for img in images]


def _image_to_png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


async def _ai_vision_transcribe(
    images: list[Image.Image],
    *,
    text_layer_hint: str = "",
) -> str:
    """Transcribe page images with the multimodal LLM."""
    from paperless_agent.llm import complete_with_images

    png_pages = [_image_to_png_bytes(img) for img in images]
    instructions = (
        "You are a careful OCR transcription engine for scanned paper documents. "
        "Transcribe all readable text from the page images. Preserve reading order, "
        "line breaks, amounts, dates, and IDs. Return plain text only — no markdown, "
        "no commentary."
    )
    hint = ""
    if text_layer_hint.strip():
        hint = (
            "\n\nEmbedded PDF text (may be incomplete or wrong; prefer the images):\n"
            f"{text_layer_hint.strip()[:4000]}"
        )
    prompt = (
        f"Transcribe these {len(png_pages)} document page image(s). "
        f"Output the full plain text.{hint}"
    )
    return await complete_with_images(
        prompt,
        images=png_pages,
        instructions=instructions,
        mime_type="image/png",
    )


async def recover_document_text(path: str | Path) -> dict[str, Any]:
    """
    Recover plain text for ingest.

    1. Optionally read a PDF embedded text layer (for workflow + AI hint)
    2. Always run AI vision OCR on page images
    """
    file_path = Path(path).expanduser().resolve()
    if not file_path.exists() or not file_path.is_file():
        return {"status": "error", "error": f"file not found: {path}"}

    suffix = file_path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        return {
            "status": "error",
            "error": f"unsupported file type: {suffix}",
            "supported": sorted(SUPPORTED_SUFFIXES),
        }

    steps: list[dict[str, Any]] = []
    text_layer = ""
    page_count: int | None = None
    filename = file_path.name

    if suffix in PDF_SUFFIXES:
        await emit_step("read", label="Read", status="running", filename=filename)
        try:
            text_layer, page_count = _extract_pdf_text_layer(file_path)
            layer_quality = assess_text_quality(text_layer)
            steps.append(
                {
                    "method": "pdf_text_layer",
                    "quality": layer_quality.__dict__,
                    "chars": layer_quality.chars,
                }
            )
            await emit_step(
                "read",
                label="Read",
                status="done",
                detail=f"text layer · {layer_quality.chars} chars",
                filename=filename,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF text layer failed for %s: %s", file_path, exc)
            steps.append({"method": "pdf_text_layer", "error": str(exc)})
            await emit_step(
                "read",
                label="Read",
                status="error",
                detail=str(exc),
                filename=filename,
            )
    else:
        await emit_step(
            "read",
            label="Read",
            status="skipped",
            detail="image scan",
            filename=filename,
        )

    text = ""
    method = "none"
    quality = assess_text_quality("")

    await emit_step("ai_ocr", label="AI OCR", status="running", filename=filename)
    try:
        images = render_document_images(file_path)
        if page_count is None:
            page_count = len(images)
        if not images:
            raise RuntimeError("no page images available for AI OCR")
        ai_text = (
            await _ai_vision_transcribe(images, text_layer_hint=text_layer)
        ).strip()
        ai_quality = assess_text_quality(ai_text)
        steps.append(
            {
                "method": "ai_vision",
                "quality": ai_quality.__dict__,
                "chars": ai_quality.chars,
                "pages_ocrd": len(images),
            }
        )
        text = ai_text
        quality = ai_quality
        method = "ai_vision"
        await emit_step(
            "ai_ocr",
            label="AI OCR",
            status="done" if ai_text else "error",
            detail=f"{ai_quality.chars} chars",
            filename=filename,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI vision OCR failed for %s: %s", file_path, exc)
        steps.append({"method": "ai_vision", "error": str(exc)})
        await emit_step(
            "ai_ocr",
            label="AI OCR",
            status="error",
            detail=str(exc),
            filename=filename,
        )
        # Last resort: keep any embedded PDF text if AI OCR failed.
        if text_layer.strip():
            text = text_layer
            quality = assess_text_quality(text)
            method = "pdf_text_layer_fallback"

    return {
        "status": "success" if text.strip() else "partial",
        "path": str(file_path),
        "filename": filename,
        "suffix": suffix,
        "text": text,
        "method": method,
        "quality": quality.__dict__,
        "page_count": page_count,
        "steps": steps,
        "used_ai_ocr": method == "ai_vision",
        "note": None
        if text.strip()
        else "No usable text recovered from AI OCR (or PDF text fallback).",
    }


def images_to_data_urls(
    images: list[bytes],
    *,
    mime_type: str = "image/png",
) -> list[str]:
    """Encode raw image bytes as data URLs for multimodal LLM APIs."""
    urls: list[str] = []
    for raw in images:
        b64 = base64.b64encode(raw).decode("ascii")
        urls.append(f"data:{mime_type};base64,{b64}")
    return urls
