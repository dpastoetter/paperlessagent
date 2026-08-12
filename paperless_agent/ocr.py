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

from paperless_agent import config
from paperless_agent.progress import emit_step, llm_busy_detail, step_label
from paperless_agent.job_control import (
    FileCancelledError,
    get_file_cancel_event,
    raise_if_cancelled,
)
from paperless_agent.tools.filesystem import IMAGE_SUFFIXES, PDF_SUFFIXES, SUPPORTED_SUFFIXES

logger = logging.getLogger(__name__)

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


def pdf_page_count(path: Path) -> int:
    """Return the number of pages in a PDF."""
    reader = PdfReader(str(path))
    return len(reader.pages)


def resolve_ocr_page_limit(
    path: Path,
    *,
    total_pages: int | None = None,
) -> int:
    """How many pages to OCR, respecting config caps."""
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return 1
    if suffix not in PDF_SUFFIXES:
        return 0

    pages = total_pages if total_pages is not None else pdf_page_count(path)
    configured = config.OCR_MAX_PAGES
    if configured <= 0:
        effective = pages
    else:
        effective = min(configured, pages)
    return min(effective, config.OCR_SAFETY_MAX_PAGES)


def render_document_page(
    path: Path,
    page_index: int,
    *,
    dpi: int | None = None,
) -> Image.Image:
    """Rasterize a single PDF page or load an image file (1-based page index)."""
    render_dpi = dpi if dpi is not None else config.OCR_DPI
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        if page_index != 1:
            raise ValueError("image files have only one page")
        with Image.open(path) as img:
            return img.convert("RGB")

    if suffix not in PDF_SUFFIXES:
        raise ValueError(f"unsupported file type for OCR: {suffix}")

    from pdf2image import convert_from_path

    images = convert_from_path(
        str(path),
        dpi=render_dpi,
        first_page=page_index,
        last_page=page_index,
        fmt="png",
    )
    if not images:
        raise RuntimeError(f"failed to render page {page_index}")
    img = images[0]
    return img.convert("RGB") if img.mode != "RGB" else img


def render_document_images(
    path: Path,
    *,
    max_pages: int | None = None,
    dpi: int | None = None,
) -> list[Image.Image]:
    """Rasterize up to max_pages from a PDF or load an image for vision OCR."""
    limit = max_pages if max_pages is not None else resolve_ocr_page_limit(path)
    if limit <= 0:
        return []
    return [render_document_page(path, i, dpi=dpi) for i in range(1, limit + 1)]


def _image_to_png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def resolve_ocr_page_timeout() -> float:
    """Per-page vision OCR timeout; Ollama on CPU needs a longer budget."""
    if config.LLM_PROVIDER == "ollama":
        return max(config.OCR_PAGE_TIMEOUT, config.OLLAMA_OCR_PAGE_TIMEOUT)
    return config.OCR_PAGE_TIMEOUT


def _ollama_vision_options() -> dict[str, int]:
    return {
        "num_ctx": config.OLLAMA_OCR_NUM_CTX,
        "num_predict": config.OLLAMA_OCR_NUM_PREDICT,
    }


def prepare_page_image_for_vision(image: Image.Image) -> tuple[bytes, str]:
    """
    Normalize a rendered page for multimodal OCR.

    Downscales large scans and uses JPEG for Ollama to keep CPU inference tractable.
    """
    img = image.convert("RGB")
    max_px = max(256, int(config.OCR_MAX_IMAGE_PX))
    width, height = img.size
    long_edge = max(width, height)
    if long_edge > max_px:
        scale = max_px / long_edge
        img = img.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.Resampling.LANCZOS,
        )

    buf = io.BytesIO()
    if config.LLM_PROVIDER == "ollama":
        img.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue(), "image/jpeg"
    img.save(buf, format="PNG")
    return buf.getvalue(), "image/png"


async def _ai_vision_transcribe_pages(
    path: Path,
    *,
    page_count: int,
    text_layer_hint: str = "",
    filename: str = "",
) -> str:
    """Transcribe each page with a separate multimodal LLM call."""
    from paperless_agent.llm import complete_with_images

    instructions = (
        "You are a careful OCR transcription engine for scanned paper documents. "
        "Transcribe all readable text from the page image. Preserve reading order, "
        "line breaks, amounts, dates, and IDs. Return plain text only — no markdown, "
        "no commentary."
    )
    ollama_options = _ollama_vision_options()
    parts: list[str] = []
    for page_index in range(1, page_count + 1):
        raise_if_cancelled()
        await emit_step(
            "ai_ocr",
            label=step_label("ai_ocr"),
            status="running",
            detail=llm_busy_detail(f"Reading page {page_index}/{page_count}…"),
            filename=filename,
        )
        img = render_document_page(path, page_index)
        image_bytes, mime_type = prepare_page_image_for_vision(img)
        hint = ""
        if page_index == 1 and text_layer_hint.strip():
            hint = (
                "\n\nEmbedded PDF text (may be incomplete or wrong; prefer the image):\n"
                f"{text_layer_hint.strip()[:4000]}"
            )
        prompt = (
            "Transcribe this document page image. Output plain text only."
            f"{hint}"
        )
        page_text = await complete_with_images(
            prompt,
            images=[image_bytes],
            instructions=instructions,
            mime_type=mime_type,
            cancel_event=get_file_cancel_event(),
            timeout=resolve_ocr_page_timeout(),
            ollama_options=ollama_options if config.LLM_PROVIDER == "ollama" else None,
        )
        parts.append(f"[Page {page_index}]\n{page_text.strip()}")
    return "\n\n".join(parts)


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

    raise_if_cancelled()
    if suffix in PDF_SUFFIXES:
        await emit_step(
            "read",
            label=step_label("read"),
            status="running",
            detail="Reading PDF text layer…",
            filename=filename,
        )
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
            pages_note = f"{page_count} page{'s' if page_count != 1 else ''}" if page_count else "PDF"
            await emit_step(
                "read",
                label=step_label("read"),
                status="done",
                detail=f"{pages_note} · {layer_quality.chars} chars in text layer",
                filename=filename,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF text layer failed for %s: %s", file_path, exc)
            steps.append({"method": "pdf_text_layer", "error": str(exc)})
            await emit_step(
                "read",
                label=step_label("read"),
                status="error",
                detail=str(exc),
                filename=filename,
            )
    else:
        await emit_step(
            "read",
            label=step_label("read"),
            status="skipped",
            detail="Image file — no PDF text layer",
            filename=filename,
        )

    text = ""
    method = "none"
    quality = assess_text_quality("")

    await emit_step(
        "ai_ocr",
        label=step_label("ai_ocr"),
        status="running",
        detail="Preparing page images…",
        filename=filename,
    )
    raise_if_cancelled()
    try:
        page_limit = resolve_ocr_page_limit(file_path, total_pages=page_count)
        if page_limit <= 0:
            raise RuntimeError("no page images available for AI OCR")
        if page_count is None:
            page_count = page_limit
        page_n = page_limit
        ai_text = (
            await _ai_vision_transcribe_pages(
                file_path,
                page_count=page_n,
                text_layer_hint=text_layer,
                filename=filename,
            )
        ).strip()
        ai_quality = assess_text_quality(ai_text)
        steps.append(
            {
                "method": "ai_vision",
                "quality": ai_quality.__dict__,
                "chars": ai_quality.chars,
                "pages_ocrd": page_n,
            }
        )
        text = ai_text
        quality = ai_quality
        method = "ai_vision"
        await emit_step(
            "ai_ocr",
            label=step_label("ai_ocr"),
            status="done" if ai_text else "error",
            detail=f"{page_n} page{'s' if page_n != 1 else ''} · {ai_quality.chars} chars",
            filename=filename,
        )
    except FileCancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI vision OCR failed for %s: %s", file_path, exc)
        steps.append({"method": "ai_vision", "error": str(exc)})
        await emit_step(
            "ai_ocr",
            label=step_label("ai_ocr"),
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
