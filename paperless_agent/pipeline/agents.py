"""ADK agents and shared filing helpers for document ingest."""

from __future__ import annotations

import json
from typing import Any

from google.adk.agents import Agent

from paperless_agent.llm import get_model
from paperless_agent.progress import emit_step_sync
from paperless_agent.settings import get_category_names
from paperless_agent.tools.filesystem import move_to_archive, propose_filename, read_document
from paperless_agent.tools.metadata_db import upsert_metadata
from paperless_agent.tools.rag_index import index_document


def file_and_persist(
    source_path: str,
    filename: str,
    doc_type: str,
    doc_date: str | None = None,
    counterparties: str | None = None,
    amount: float | None = None,
    currency: str | None = None,
    summary: str | None = None,
    extracted_json: str | None = None,
    full_text: str | None = None,
    checksum: str | None = None,
    content_hash: str | None = None,
) -> dict[str, Any]:
    """
    Move the source file into the archive, save SQLite metadata, and RAG-index it.

    Call this once you have finalized doc_type, filename, and extracted fields.
    """
    source_name = source_path.rsplit("/", 1)[-1]
    year = None
    if doc_date and len(doc_date) >= 4:
        year = doc_date[:4]

    emit_step_sync("file", label="File", status="running", filename=source_name)
    moved = move_to_archive(
        source_path=source_path,
        filename=filename,
        doc_type=doc_type,
        year=year,
    )
    if moved.get("status") != "success":
        emit_step_sync(
            "file",
            label="File",
            status="error",
            detail=moved.get("error"),
            filename=source_name,
        )
        return moved

    original_name = source_name
    saved = upsert_metadata(
        original_name=original_name,
        filename=moved["filename"],
        path=moved["archive_path"],
        doc_type=doc_type,
        doc_date=doc_date,
        counterparties=counterparties,
        amount=amount,
        currency=currency,
        summary=summary,
        extracted_json=extracted_json,
        checksum=checksum,
        content_hash=content_hash,
    )
    if saved.get("status") != "success":
        emit_step_sync(
            "file",
            label="File",
            status="error",
            detail=saved.get("error"),
            filename=source_name,
        )
        return saved

    emit_step_sync(
        "file",
        label="File",
        status="done",
        detail=moved.get("archive_path"),
        filename=source_name,
    )

    document_id = saved["document_id"]
    text_for_index = full_text or summary or ""
    if extracted_json and not text_for_index:
        text_for_index = extracted_json
    emit_step_sync("index", label="Index", status="running", filename=source_name)
    indexed = index_document(
        document_id=document_id,
        text=text_for_index,
        filename=moved["filename"],
        doc_type=doc_type,
    )
    emit_step_sync(
        "index",
        label="Index",
        status="done" if indexed.get("status") == "success" else "error",
        detail=(
            f"{indexed.get('chunk_count')} chunks"
            if indexed.get("status") == "success"
            else indexed.get("error")
        ),
        filename=source_name,
    )
    return {
        "status": "success" if indexed.get("status") == "success" else "partial",
        "document_id": document_id,
        "archive_path": moved["archive_path"],
        "filename": moved["filename"],
        "metadata": saved.get("document"),
        "index": indexed,
    }


def build_pipeline_agent() -> Agent:
    """
    Single ADK agent for `adk web` debugging.

    Production ingest uses paperless_agent.ingest.ingest_document to avoid
    SequentialAgent session-state template failures with Codex streaming.
    """
    type_list = ", ".join(get_category_names())
    return Agent(
        model=get_model(),
        name="paperless_ingest",
        description="Ingests a scanned document into the local paperless archive.",
        instruction=(
            "You ingest one scanned document into a personal archive.\n"
            f"Allowed doc_type values: {type_list}.\n"
            "1. Call read_document with the absolute source_path from the user.\n"
            "2. Decide doc_type, doc_date (ISO), counterparties, amount, currency, "
            "summary, and full_text from the document content/filename.\n"
            "3. Call propose_filename with those fields and original_path.\n"
            "4. Call file_and_persist with source_path, the proposed filename, "
            "and the extracted fields (pass extracted_json as a JSON string).\n"
            "5. Confirm document_id, archive_path, and filename.\n"
            "Do not use curly-brace template placeholders."
        ),
        tools=[read_document, propose_filename, file_and_persist],
    )


def parse_json_blob(value: Any) -> dict[str, Any]:
    """Best-effort parse of agent JSON output that may include markdown fences."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    text = value.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}
