"""Deterministic document ingest: extract via LLM, then file + index in code."""

from __future__ import annotations

import json
from typing import Any

from pathlib import Path

from paperless_agent import config
from paperless_agent.dedup import content_hash, file_checksum, find_duplicates
from paperless_agent.job_control import (
    FileCancelledError,
    get_file_cancel_event,
    raise_if_cancelled,
)
from paperless_agent.llm import complete_text
from paperless_agent.ocr import recover_document_text
from paperless_agent.pipeline.agents import file_and_persist, parse_json_blob
from paperless_agent.progress import emit_step, llm_busy_detail, step_label
from paperless_agent.review import create_review, pending_checksums
from paperless_agent.settings import get_category_names, review_approval_required
from paperless_agent.tools.filesystem import propose_filename

_EXTRACT_SCHEMA_HINT = """{
  "doc_type": "<one of the allowed category names>",
  "doc_date": "YYYY-MM-DD or null",
  "subject": "short topic or document title (5-12 words)",
  "parties": "comma-separated people, companies, institutions",
  "reference_ids": ["optional IDs, policy numbers, case refs"],
  "amount": null,
  "currency": null,
  "summary": "2-4 sentence summary",
  "full_text": "optional — omit; backend uses OCR text for indexing"
}"""


_FIELD_INSTRUCTIONS = (
    "You extract structured metadata from personal and household paper documents "
    "(letters, medical records, IDs, bills, contracts, tax, insurance, etc.). "
    "Reply with ONLY valid JSON, no markdown fences.\n"
    "Guidelines:\n"
    "- subject: short topic or title (what the document is about).\n"
    "- parties: sender, recipient, issuer, doctor, employer, insurer, etc.\n"
    "- reference_ids: policy numbers, case refs, invoice numbers when present.\n"
    "- amount and currency: ONLY for invoices, receipts, bills, bank/tax/utility "
    "documents when a monetary value is stated; otherwise null.\n"
    "- Pick doc_type from the allowed list; use 'other' when unsure."
)


def _text_for_extract_prompt(text: str, max_chars: int) -> str:
    """Sample long OCR text for the metadata extraction prompt."""
    cleaned = (text or "").strip()
    if not cleaned:
        return "[no extractable text — infer from filename]"
    if max_chars <= 0 or len(cleaned) <= max_chars:
        return cleaned
    head_len = int(max_chars * 2 / 3)
    tail_len = max_chars - head_len
    omitted = len(cleaned) - head_len - tail_len
    note = (
        f"(Document is {len(cleaned)} characters; "
        f"middle {omitted} chars omitted for extraction prompt.)\n\n"
    )
    return (
        note
        + cleaned[:head_len]
        + "\n\n[... middle omitted ...]\n\n"
        + cleaned[-tail_len:]
    )


def normalize_extracted_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize LLM extraction output to consistent field names and types."""
    parties_raw = raw.get("parties")
    if parties_raw is None:
        parties_raw = raw.get("counterparties")
    if isinstance(parties_raw, str):
        parties = parties_raw.strip()
    elif parties_raw is None:
        parties = ""
    else:
        parties = str(parties_raw).strip()

    subject_raw = raw.get("subject")
    if isinstance(subject_raw, str):
        subject = subject_raw.strip()
    elif subject_raw is None:
        subject = ""
    else:
        subject = str(subject_raw).strip()

    ref_ids = raw.get("reference_ids")
    if ref_ids is None:
        ref_ids = raw.get("ids")
    if isinstance(ref_ids, str):
        reference_ids = [ref_ids.strip()] if ref_ids.strip() else []
    elif isinstance(ref_ids, list):
        reference_ids = [str(item).strip() for item in ref_ids if str(item).strip()]
    else:
        reference_ids = []

    amount = raw.get("amount")
    if amount is not None and amount != "":
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            amount = None
    else:
        amount = None

    currency_raw = raw.get("currency")
    if isinstance(currency_raw, str) and currency_raw.strip():
        currency = currency_raw.strip().upper()
    else:
        currency = None

    normalized = dict(raw)
    normalized["parties"] = parties or None
    normalized["counterparties"] = parties or None
    normalized["subject"] = subject or None
    normalized["reference_ids"] = reference_ids
    normalized["amount"] = amount
    normalized["currency"] = currency if amount is not None else None
    return normalized


async def extract_document_fields(source_path: str) -> dict[str, Any]:
    """Recover document text via AI OCR, then extract structured fields via LLM."""
    raise_if_cancelled()
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
        f"{_text_for_extract_prompt(text, config.EXTRACT_MAX_CHARS)}\n\n"
        f"Return ONLY JSON matching this shape:\n{_EXTRACT_SCHEMA_HINT}"
    )

    await emit_step(
        "extract",
        label=step_label("extract"),
        status="running",
        detail=llm_busy_detail("Extracting type, date, subject, parties"),
        filename=filename,
    )
    try:
        raw = await complete_text(
            prompt,
            instructions=_FIELD_INSTRUCTIONS,
            cancel_event=get_file_cancel_event(),
        )
    except FileCancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        await emit_step(
            "extract",
            label=step_label("extract"),
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

    fields = normalize_extracted_fields(parse_json_blob(raw))
    if not fields.get("doc_type"):
        await emit_step(
            "extract",
            label=step_label("extract"),
            status="error",
            detail="Model returned invalid JSON",
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

    # Always prefer recovered OCR text for indexing over LLM output.
    if text:
        fields["full_text"] = text

    doc_type_label = str(fields.get("doc_type") or "other")
    await emit_step(
        "extract",
        label=step_label("extract"),
        status="done",
        detail=f"Type: {doc_type_label}",
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
    raise_if_cancelled()
    if extracted.get("status") != "success":
        return extracted

    categories = get_category_names()
    doc_type = str(extracted.get("doc_type") or "other").lower()
    if doc_type not in categories:
        doc_type = "other" if "other" in categories else categories[0]
    doc_date = extracted.get("doc_date")
    counterparties = extracted.get("counterparties") or ""
    subject = extracted.get("subject")
    amount = extracted.get("amount")
    currency = extracted.get("currency")
    summary = extracted.get("summary") or ""
    full_text = extracted.get("full_text") or extracted["_source"].get("text") or summary

    first_party = None
    if isinstance(counterparties, str) and counterparties.strip():
        first_party = counterparties.split(",")[0].strip()

    await emit_step(
        "name",
        label=step_label("name"),
        status="running",
        detail="Building a clear filename…",
        filename=filename,
    )
    raise_if_cancelled()
    named = propose_filename(
        doc_type=doc_type,
        doc_date=doc_date if isinstance(doc_date, str) else None,
        counterparty=first_party,
        subject=subject if isinstance(subject, str) else None,
        amount=float(amount) if isinstance(amount, (int, float)) else None,
        currency=currency if isinstance(currency, str) else None,
        original_path=source_path,
    )
    if named.get("status") != "success":
        await emit_step(
            "name",
            label=step_label("name"),
            status="error",
            detail=named.get("error"),
            filename=filename,
        )
        return named
    await emit_step(
        "name",
        label=step_label("name"),
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
    raise_if_cancelled()
    await emit_step(
        "review",
        label=step_label("review"),
        status="running",
        detail="Checking for duplicates…",
        filename=filename,
    )
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
            "subject": subject if isinstance(subject, str) else None,
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
            label=step_label("review"),
            status="done",
            detail=f"Waiting for your approval{dup_note}",
            filename=filename,
        )
        for step_id in ("file", "index"):
            await emit_step(
                step_id,
                label=step_label(step_id),
                status="skipped",
                detail="After you approve",
                filename=filename,
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
        label=step_label("review"),
        status="done",
        detail="Auto-approved (no duplicates)",
        filename=filename,
    )

    result = file_and_persist(
        source_path=source_path,
        filename=named["filename"],
        doc_type=doc_type,
        doc_date=doc_date if isinstance(doc_date, str) else None,
        subject=subject if isinstance(subject, str) else None,
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
