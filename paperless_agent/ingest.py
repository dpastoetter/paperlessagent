"""Deterministic document ingest: extract via LLM, then file + index in code."""

from __future__ import annotations

import json
from typing import Any

from pathlib import Path

from paperless_agent.dedup import content_hash, file_checksum, find_duplicates
from paperless_agent.llm import complete_text
from paperless_agent.ocr import recover_document_text
from paperless_agent.pipeline.agents import file_and_persist, parse_json_blob
from paperless_agent.progress import emit_step
from paperless_agent.review import create_review, pending_checksums
from paperless_agent.settings import get_category_names, review_approval_required
from paperless_agent.tools.filesystem import propose_filename

_EXTRACT_SCHEMA_HINT = """{
  "doc_type": "<one of the allowed category names>",
  "doc_date": "YYYY-MM-DD or null",
  "counterparties": "comma-separated names",
  "amount": 0.0,
  "currency": "EUR",
  "ids": ["optional ids"],
  "summary": "2-4 sentence summary",
  "full_text": "best-effort plain text for search indexing"
}"""


_FIELD_INSTRUCTIONS = (
    "You extract structured metadata from scanned paper documents. "
    "Reply with ONLY valid JSON, no markdown fences."
)


async def extract_document_fields(source_path: str) -> dict[str, Any]:
    """Recover document text via AI OCR, then extract structured fields via LLM."""
    recovery = await recover_document_text(source_path)
    if recovery.get("status") not in {"success", "partial"}:
        return recovery

    filename = recovery.get("filename") or Path(source_path).name
    categories = get_category_names()
    type_list = ", ".join(categories)
    text = (recovery.get("text") or "").strip()
    method = recovery.get("method") or "none"
    quality = recovery.get("quality") or {}
    prompt = (
        f"Document path: {recovery['path']}\n"
        f"Filename: {recovery['filename']}\n"
        f"Text recovery method: {method}\n"
        f"Text quality: {quality}\n"
        f"Allowed doc_type values: {type_list}\n\n"
        f"Document text (may be empty for scans):\n"
        f"{text[:12000] or '[no extractable text — infer from filename]'}\n\n"
        f"Return ONLY JSON matching this shape:\n{_EXTRACT_SCHEMA_HINT}"
    )

    await emit_step("extract", label="Extract", status="running", filename=filename)
    try:
        raw = await complete_text(prompt, instructions=_FIELD_INSTRUCTIONS)
    except Exception as exc:  # noqa: BLE001
        await emit_step(
            "extract",
            label="Extract",
            status="error",
            detail=str(exc),
            filename=filename,
        )
        return {
            "status": "error",
            "error": f"LLM extraction failed: {exc}",
            "recovery": {
                "method": method,
                "quality": quality,
                "steps": recovery.get("steps"),
            },
        }

    fields = parse_json_blob(raw)
    if not fields:
        await emit_step(
            "extract",
            label="Extract",
            status="error",
            detail="invalid JSON",
            filename=filename,
        )
        return {
            "status": "error",
            "error": "LLM did not return valid JSON",
            "raw": raw[:2000],
            "recovery": {
                "method": method,
                "quality": quality,
                "steps": recovery.get("steps"),
            },
        }

    # Prefer recovered OCR text for indexing when the model omits full_text.
    if not (fields.get("full_text") or "").strip() and text:
        fields["full_text"] = text

    await emit_step(
        "extract",
        label="Extract",
        status="done",
        detail=str(fields.get("doc_type") or "other"),
        filename=filename,
    )
    fields["status"] = "success"
    fields["ocr_method"] = method
    fields["ocr_quality"] = quality
    fields["used_ai_ocr"] = bool(recovery.get("used_ai_ocr"))
    fields["_source"] = recovery
    return fields


async def ingest_document(source_path: str) -> dict[str, Any]:
    """
    End-to-end ingest without ADK SequentialAgent state templating.

    1) AI OCR → text  2) LLM extract JSON  3) Name  4) File + metadata + RAG
    """
    filename = Path(source_path).name
    extracted = await extract_document_fields(source_path)
    if extracted.get("status") != "success":
        return extracted

    categories = get_category_names()
    doc_type = str(extracted.get("doc_type") or "other").lower()
    if doc_type not in categories:
        doc_type = "other" if "other" in categories else categories[0]
    doc_date = extracted.get("doc_date")
    counterparties = extracted.get("counterparties") or ""
    amount = extracted.get("amount")
    currency = extracted.get("currency")
    summary = extracted.get("summary") or ""
    full_text = extracted.get("full_text") or extracted["_source"].get("text") or summary

    first_party = None
    if isinstance(counterparties, str) and counterparties.strip():
        first_party = counterparties.split(",")[0].strip()

    await emit_step("name", label="Name", status="running", filename=filename)
    named = propose_filename(
        doc_type=doc_type,
        doc_date=doc_date if isinstance(doc_date, str) else None,
        counterparty=first_party,
        amount=float(amount) if isinstance(amount, (int, float)) else None,
        currency=currency if isinstance(currency, str) else None,
        original_path=source_path,
    )
    if named.get("status") != "success":
        await emit_step(
            "name",
            label="Name",
            status="error",
            detail=named.get("error"),
            filename=filename,
        )
        return named
    await emit_step(
        "name",
        label="Name",
        status="done",
        detail=named["filename"],
        filename=filename,
    )

    extracted_for_db = {
        k: v
        for k, v in extracted.items()
        if not k.startswith("_") and k != "status"
    }

    # Duplicate check + human-in-the-loop gate before any filesystem writes.
    await emit_step("review", label="Review", status="running", filename=filename)
    checksum = file_checksum(source_path)
    text_hash = content_hash(full_text if isinstance(full_text, str) else None)
    duplicates = find_duplicates(
        checksum, full_text if isinstance(full_text, str) else None
    )
    if checksum in pending_checksums():
        duplicates.insert(
            0,
            {"kind": "pending", "filename": filename, "score": 1.0},
        )

    require_approval = review_approval_required()
    if require_approval or duplicates:
        proposal = {
            "filename": named["filename"],
            "doc_type": doc_type,
            "doc_date": doc_date if isinstance(doc_date, str) else None,
            "counterparties": counterparties if isinstance(counterparties, str) else None,
            "amount": float(amount) if isinstance(amount, (int, float)) else None,
            "currency": currency if isinstance(currency, str) else None,
            "summary": summary if isinstance(summary, str) else None,
            "full_text": full_text if isinstance(full_text, str) else None,
            "ocr_method": extracted.get("ocr_method"),
        }
        queued = create_review(
            source_path=source_path,
            original_name=filename,
            proposal=proposal,
            checksum=checksum,
            content_hash=text_hash,
            duplicates=duplicates,
        )
        dup_note = f", {len(duplicates)} possible duplicate(s)" if duplicates else ""
        await emit_step(
            "review",
            label="Review",
            status="done",
            detail=f"queued for approval{dup_note}",
            filename=filename,
        )
        for step_id, label in (("file", "File"), ("index", "Index")):
            await emit_step(
                step_id, label=label, status="skipped", filename=filename
            )
        return {
            "status": "pending_review",
            "review_id": queued["review_id"],
            "proposed_filename": named["filename"],
            "duplicates": duplicates,
            "message": (
                "Queued for review"
                + (f" ({len(duplicates)} possible duplicate(s))" if duplicates else "")
            ),
        }

    await emit_step(
        "review",
        label="Review",
        status="done",
        detail="auto-approved (no duplicates)",
        filename=filename,
    )

    result = file_and_persist(
        source_path=source_path,
        filename=named["filename"],
        doc_type=doc_type,
        doc_date=doc_date if isinstance(doc_date, str) else None,
        counterparties=counterparties if isinstance(counterparties, str) else None,
        amount=float(amount) if isinstance(amount, (int, float)) else None,
        currency=currency if isinstance(currency, str) else None,
        summary=summary if isinstance(summary, str) else None,
        extracted_json=json.dumps(extracted_for_db, ensure_ascii=False),
        full_text=full_text if isinstance(full_text, str) else None,
        checksum=checksum,
        content_hash=text_hash,
    )
    return {
        **result,
        "extracted": extracted_for_db,
        "proposed_filename": named["filename"],
    }
