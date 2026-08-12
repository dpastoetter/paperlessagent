"""Deterministic archive Q&A: retrieve evidence, then answer with the LLM."""

from __future__ import annotations

from typing import Any

from paperless_agent.llm import complete_text
from paperless_agent.tools.metadata_db import list_recent, search_metadata
from paperless_agent.tools.rag_index import retrieve_chunks

_ASK_INSTRUCTIONS = (
    "You are a local paperless archive assistant. "
    "Answer using ONLY the provided evidence from retrieved chunks and metadata. "
    "Cite filename and document_id for each factual claim. "
    "If evidence is missing or weak, say so clearly. "
    "Prefer a concise answer, then a short Sources section."
)


def _format_chunks(chunks: list[dict[str, Any]]) -> str:
    if not chunks:
        return "(no semantic chunks retrieved)"
    lines: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        lines.append(
            f"[{i}] filename={chunk.get('filename')} "
            f"document_id={chunk.get('document_id')} "
            f"doc_type={chunk.get('doc_type')} "
            f"distance={chunk.get('distance')}\n"
            f"{(chunk.get('text') or '').strip()}"
        )
    return "\n\n".join(lines)


def _format_documents(documents: list[dict[str, Any]]) -> str:
    if not documents:
        return "(no metadata matches)"
    lines: list[str] = []
    for doc in documents:
        lines.append(
            f"- filename={doc.get('filename')} document_id={doc.get('id')} "
            f"doc_type={doc.get('doc_type')} doc_date={doc.get('doc_date')} "
            f"subject={doc.get('subject')} "
            f"counterparties={doc.get('counterparties')} "
            f"amount={doc.get('amount')} {doc.get('currency') or ''}\n"
            f"  summary: {(doc.get('summary') or '').strip()}"
        )
    return "\n".join(lines)


def _source_entry(
    *,
    document_id: str,
    filename: str | None,
    doc_type: str | None,
) -> dict[str, Any]:
    doc_id = str(document_id)
    return {
        "document_id": doc_id,
        "filename": filename,
        "doc_type": doc_type,
        "open_url": f"/api/documents/{doc_id}/file",
        "reveal_url": f"/api/documents/{doc_id}/reveal",
    }


def _collect_sources(
    chunks: list[dict[str, Any]],
    documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    sources: list[dict[str, Any]] = []
    for chunk in chunks:
        doc_id = chunk.get("document_id")
        if not doc_id or doc_id in seen:
            continue
        seen.add(str(doc_id))
        sources.append(
            _source_entry(
                document_id=str(doc_id),
                filename=chunk.get("filename"),
                doc_type=chunk.get("doc_type"),
            )
        )
    for doc in documents:
        doc_id = doc.get("id")
        if not doc_id or doc_id in seen:
            continue
        seen.add(str(doc_id))
        sources.append(
            _source_entry(
                document_id=str(doc_id),
                filename=doc.get("filename"),
                doc_type=doc.get("doc_type"),
            )
        )
    return sources


async def ask_archive(question: str) -> dict[str, Any]:
    """
    Answer a natural-language question over the local archive.

    Uses Chroma retrieval + metadata search for evidence, then a direct LLM
    completion (no ADK tool loop) so ChatGPT OAuth/Codex returns usable text.
    """
    q = (question or "").strip()
    if not q:
        return {"status": "error", "reply": "Question is empty.", "error": "empty question"}

    retrieved = retrieve_chunks(q)
    if retrieved.get("status") != "success":
        return {
            "status": "error",
            "reply": retrieved.get("error") or "Retrieval failed",
            "error": retrieved.get("error"),
            "retrieval": retrieved,
        }

    chunks = retrieved.get("chunks") or []
    meta = search_metadata(query=q, limit=10)
    documents = meta.get("documents") or []

    # Broad questions / weak local embeddings: always include recent docs as context.
    if not documents:
        recent = list_recent(limit=8)
        documents = recent.get("documents") or []

    prompt = (
        f"User question:\n{q}\n\n"
        f"Retrieved chunks:\n{_format_chunks(chunks)}\n\n"
        f"Metadata matches / recent documents:\n{_format_documents(documents)}\n\n"
        "Write the answer now."
    )

    try:
        reply = await complete_text(prompt, instructions=_ASK_INSTRUCTIONS)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "reply": f"LLM answer failed: {exc}",
            "error": str(exc),
            "retrieval": retrieved,
            "metadata_count": len(documents),
        }

    if not reply:
        return {
            "status": "error",
            "reply": (
                "The model returned an empty answer. Try again, or check Settings → "
                "AI provider (Ollama model pulled, or cloud sign-in / API key)."
            ),
            "error": "empty model reply",
            "retrieval": retrieved,
            "metadata_count": len(documents),
        }

    sources = _collect_sources(chunks, documents)
    return {
        "status": "success",
        "reply": reply,
        "sources": sources,
        "retrieval_count": len(chunks),
        "metadata_count": len(documents),
    }
