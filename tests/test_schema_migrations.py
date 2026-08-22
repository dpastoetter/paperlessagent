"""SQLite schema migration tests."""

from __future__ import annotations

import sqlite3

import pytest

from deepcatalog.tools import metadata_db


@pytest.fixture()
def legacy_db(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    monkeypatch.setattr(metadata_db, "DB_PATH", db_path)
    monkeypatch.setattr("deepcatalog.config.DB_PATH", db_path)
    monkeypatch.setattr("deepcatalog.config.DATA_DIR", tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE documents (
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
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
    return db_path


def test_init_db_migrates_legacy_schema(legacy_db):
    metadata_db.init_db()
    with sqlite3.connect(legacy_db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
        assert {"checksum", "content_hash", "subject"}.issubset(cols)
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == metadata_db.SCHEMA_VERSION


def test_init_db_is_idempotent(legacy_db):
    metadata_db.init_db()
    metadata_db.init_db()
    with sqlite3.connect(legacy_db) as conn:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == metadata_db.SCHEMA_VERSION


def test_ensure_column_reraises_unexpected_errors(monkeypatch):
    class FakeConn:
        def execute(self, *_a, **_k):
            raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(metadata_db, "_table_columns", lambda *_a, **_k: set())
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        metadata_db._ensure_column(FakeConn(), "documents", "checksum", "TEXT")


def test_duplicate_column_error_is_recognized():
    assert metadata_db._is_duplicate_column_error(
        sqlite3.OperationalError("duplicate column name: checksum")
    )
    assert not metadata_db._is_duplicate_column_error(sqlite3.OperationalError("disk I/O error"))
