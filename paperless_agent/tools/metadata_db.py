"""SQLite metadata store for archived documents."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from paperless_agent.config import DB_PATH, ensure_data_dirs


def _connect() -> sqlite3.Connection:
    ensure_data_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # The API server and the background inbox poller both write this DB.
    # WAL + a generous busy timeout prevent "database is locked" errors.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    """Create the documents table if it does not exist."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                original_name TEXT NOT NULL,
                filename TEXT NOT NULL,
                path TEXT NOT NULL,
                doc_type TEXT,
                doc_date TEXT,
                counterparties TEXT,
                amount REAL,
                currency TEXT,
                summary TEXT,
                extracted_json TEXT,
                created_at TEXT NOT NULL,
                indexed_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_doc_type ON documents(doc_type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_doc_date ON documents(doc_date)"
        )
        # Duplicate-detection columns (migration for pre-existing databases).
        for ddl in (
            "ALTER TABLE documents ADD COLUMN checksum TEXT",
            "ALTER TABLE documents ADD COLUMN content_hash TEXT",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_checksum ON documents(checksum)"
        )
        conn.commit()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    if data.get("extracted_json"):
        try:
            data["extracted"] = json.loads(data["extracted_json"])
        except json.JSONDecodeError:
            data["extracted"] = None
    return data


def upsert_metadata(
    original_name: str,
    filename: str,
    path: str,
    doc_type: str = "other",
    doc_date: str | None = None,
    counterparties: str | None = None,
    amount: float | None = None,
    currency: str | None = None,
    summary: str | None = None,
    extracted_json: str | None = None,
    document_id: str | None = None,
    checksum: str | None = None,
    content_hash: str | None = None,
) -> dict[str, Any]:
    """Insert or update a document metadata row. Returns the saved record."""
    init_db()
    doc_id = document_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    with _connect() as conn:
        existing = conn.execute(
            "SELECT id, created_at FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE documents SET
                    original_name = ?,
                    filename = ?,
                    path = ?,
                    doc_type = ?,
                    doc_date = ?,
                    counterparties = ?,
                    amount = ?,
                    currency = ?,
                    summary = ?,
                    extracted_json = ?,
                    checksum = ?,
                    content_hash = ?
                WHERE id = ?
                """,
                (
                    original_name,
                    filename,
                    path,
                    doc_type,
                    doc_date,
                    counterparties,
                    amount,
                    currency,
                    summary,
                    extracted_json,
                    checksum,
                    content_hash,
                    doc_id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO documents (
                    id, original_name, filename, path, doc_type, doc_date,
                    counterparties, amount, currency, summary, extracted_json,
                    checksum, content_hash, created_at, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    doc_id,
                    original_name,
                    filename,
                    path,
                    doc_type,
                    doc_date,
                    counterparties,
                    amount,
                    currency,
                    summary,
                    extracted_json,
                    checksum,
                    content_hash,
                    now,
                ),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()

    return {
        "status": "success",
        "document_id": doc_id,
        "document": _row_to_dict(row),
    }


def mark_indexed(document_id: str) -> dict[str, Any]:
    """Set indexed_at timestamp after RAG indexing succeeds."""
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "UPDATE documents SET indexed_at = ? WHERE id = ?",
            (now, document_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
    if row is None:
        return {"status": "error", "error": f"document_id not found: {document_id}"}
    return {"status": "success", "document": _row_to_dict(row)}


def get_document(document_id: str) -> dict[str, Any]:
    """Fetch a single document by id."""
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
    if row is None:
        return {"status": "error", "error": f"document_id not found: {document_id}"}
    return {"status": "success", "document": _row_to_dict(row)}


def search_metadata(
    query: str | None = None,
    doc_type: str | None = None,
    counterparty: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Search document metadata with optional filters."""
    init_db()
    clauses: list[str] = []
    params: list[Any] = []

    if query:
        clauses.append(
            "(filename LIKE ? OR summary LIKE ? OR counterparties LIKE ? "
            "OR extracted_json LIKE ? OR original_name LIKE ?)"
        )
        like = f"%{query}%"
        params.extend([like, like, like, like, like])
    if doc_type:
        clauses.append("doc_type = ?")
        params.append(doc_type)
    if counterparty:
        clauses.append("counterparties LIKE ?")
        params.append(f"%{counterparty}%")
    if date_from:
        clauses.append("doc_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("doc_date <= ?")
        params.append(date_to)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        f"SELECT * FROM documents {where} "
        "ORDER BY COALESCE(doc_date, created_at) DESC LIMIT ?"
    )
    params.append(max(1, min(limit, 100)))

    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    return {
        "status": "success",
        "count": len(rows),
        "documents": [_row_to_dict(r) for r in rows],
    }


def list_recent(limit: int = 50) -> dict[str, Any]:
    """Return recently created documents."""
    return search_metadata(limit=limit)
