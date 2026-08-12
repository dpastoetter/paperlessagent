"""Helpers to run ingest and query flows programmatically."""

from __future__ import annotations

from typing import Any

from paperless_agent.ask import ask_archive
from paperless_agent.ingest import ingest_document
from paperless_agent.job_control import FileCancelledError


async def run_pipeline_on_path(source_path: str) -> dict[str, Any]:
    """Run deterministic ingest on a local file path."""
    try:
        result = await ingest_document(source_path)
    except FileCancelledError as exc:
        return {
            "status": "cancelled",
            "reply": str(exc),
            "result": {"status": "cancelled", "error": str(exc)},
        }
    status = result.get("status")
    if status in {"success", "partial"}:
        return {
            "status": status,
            "reply": (
                f"Filed {result.get('filename')} → {result.get('archive_path')} "
                f"(document_id={result.get('document_id')})"
            ),
            "result": result,
        }
    if status == "pending_review":
        return {
            "status": "pending_review",
            "reply": result.get("message") or "Queued for review",
            "result": result,
        }
    return {
        "status": "error",
        "reply": result.get("error") or "Ingest failed",
        "result": result,
    }


async def run_query(question: str) -> dict[str, Any]:
    """Answer an archive question via retrieve + direct LLM completion."""
    return await ask_archive(question)
