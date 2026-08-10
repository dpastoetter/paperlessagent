"""Duplicate detection: file checksums and text-content similarity."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from paperless_agent.tools.metadata_db import _connect, init_db

# Word-set Jaccard similarity above this counts as a near-duplicate.
SIMILARITY_THRESHOLD = 0.82
# Only compare against this many most recent documents.
MAX_CANDIDATES = 400


def file_checksum(path: str | Path) -> str:
    """SHA-256 hex digest of a file's bytes."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(text: str | None) -> str:
    """Lowercase alphanumeric-token form of text, for stable content hashing."""
    if not text:
        return ""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return " ".join(tokens)


def content_hash(text: str | None) -> str | None:
    """SHA-256 of normalized text; None when there is no usable text."""
    normalized = normalize_text(text)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def text_similarity(a: str | None, b: str | None) -> float:
    """Jaccard similarity over word sets of normalized texts (0..1)."""
    set_a = set(normalize_text(a).split())
    set_b = set(normalize_text(b).split())
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0


def _document_text(row: sqlite3.Row) -> str:
    """Best available text for a stored document (full_text, else summary)."""
    raw = row["extracted_json"]
    if raw:
        try:
            extracted = json.loads(raw)
            full_text = extracted.get("full_text")
            if isinstance(full_text, str) and full_text.strip():
                return full_text
        except json.JSONDecodeError:
            pass
    return row["summary"] or ""


def find_duplicates(
    checksum: str,
    text: str | None,
    *,
    threshold: float = SIMILARITY_THRESHOLD,
) -> list[dict[str, Any]]:
    """
    Find likely duplicates of a new document among archived documents.

    Returns matches ordered strongest first:
    - kind "exact": identical file bytes (checksum match)
    - kind "content": identical normalized text (content hash match)
    - kind "similar": word-set similarity >= threshold
    """
    init_db()
    new_content = content_hash(text)
    matches: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, filename, path, checksum, content_hash, extracted_json, summary "
            "FROM documents ORDER BY created_at DESC LIMIT ?",
            (MAX_CANDIDATES,),
        ).fetchall()

    for row in rows:
        if row["checksum"] and row["checksum"] == checksum:
            matches.append(
                {
                    "kind": "exact",
                    "document_id": row["id"],
                    "filename": row["filename"],
                    "score": 1.0,
                }
            )
            seen_ids.add(row["id"])

    if new_content:
        for row in rows:
            if row["id"] in seen_ids:
                continue
            if row["content_hash"] and row["content_hash"] == new_content:
                matches.append(
                    {
                        "kind": "content",
                        "document_id": row["id"],
                        "filename": row["filename"],
                        "score": 1.0,
                    }
                )
                seen_ids.add(row["id"])

    if text and normalize_text(text):
        for row in rows:
            if row["id"] in seen_ids:
                continue
            score = text_similarity(text, _document_text(row))
            if score >= threshold:
                matches.append(
                    {
                        "kind": "similar",
                        "document_id": row["id"],
                        "filename": row["filename"],
                        "score": round(score, 3),
                    }
                )
                seen_ids.add(row["id"])

    matches.sort(key=lambda m: ({"exact": 0, "content": 1, "similar": 2}[m["kind"]], -m["score"]))
    return matches
