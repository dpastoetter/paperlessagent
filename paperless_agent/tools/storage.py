"""Destructive storage helpers: wipe archive files, SQLite metadata, and Chroma."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from paperless_agent import config
from paperless_agent.settings import get_source_dir, load_settings
from paperless_agent.tools.metadata_db import init_db, list_recent


def _safe_rmtree(path: Path) -> bool:
    if not path.exists():
        return False
    if path.is_file():
        path.unlink(missing_ok=True)
        return True
    shutil.rmtree(path, ignore_errors=False)
    return True


def _wipe_dir_contents(path: Path) -> int:
    """Delete everything inside a directory; recreate the empty directory."""
    removed = 0
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        return 0
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=False)
        else:
            child.unlink(missing_ok=True)
        removed += 1
    path.mkdir(parents=True, exist_ok=True)
    return removed


def clear_all_stored_data() -> dict[str, Any]:
    """
    Remove archived files, inbox scans, SQLite metadata, and the Chroma RAG store.

    Keeps Setup settings (settings.json) and app code. Recreates empty data dirs.
    """
    config.ensure_data_dirs()
    settings = load_settings()

    deleted_files = 0
    missing_files = 0
    docs = list_recent(limit=10000).get("documents") or []
    for doc in docs:
        path_value = doc.get("path")
        if not path_value:
            continue
        path = Path(path_value).expanduser()
        try:
            path = path.resolve()
        except OSError:
            missing_files += 1
            continue
        if path.is_file():
            path.unlink(missing_ok=True)
            deleted_files += 1
        else:
            missing_files += 1

    # Wipe configured category folders + default archive tree
    wiped_dirs: list[str] = []
    archive_dir = Path(config.ARCHIVE_DIR).resolve()
    _wipe_dir_contents(archive_dir)
    wiped_dirs.append(str(archive_dir))

    for cat in settings.get("categories") or []:
        folder = Path(cat.get("folder") or "").expanduser()
        if not str(folder):
            continue
        try:
            folder = folder.resolve()
        except OSError:
            continue
        if folder == archive_dir or str(folder).startswith(str(archive_dir) + "/"):
            continue
        _wipe_dir_contents(folder)
        wiped_dirs.append(str(folder))

    # Clear source/inbox scans
    source_dir = get_source_dir()
    inbox_removed = _wipe_dir_contents(source_dir)

    # Reset SQLite
    db_path = Path(config.DB_PATH).resolve()
    db_removed = False
    if db_path.exists():
        db_path.unlink(missing_ok=True)
        db_removed = True
    # Also drop leftover journal/wal if present
    for suffix in ("-wal", "-shm", "-journal"):
        side = Path(str(db_path) + suffix)
        if side.exists():
            side.unlink(missing_ok=True)
    init_db()

    # Reset Chroma (clear in-process client cache so a new empty store is used)
    chroma_dir = Path(config.CHROMA_DIR).resolve()
    try:
        import chromadb
        from chromadb.api.client import SharedSystemClient

        try:
            client = chromadb.PersistentClient(path=str(chroma_dir))
            client.delete_collection("paperless_chunks")
        except Exception:
            pass
        SharedSystemClient.clear_system_cache()
    except Exception:
        pass
    chroma_removed = _safe_rmtree(chroma_dir)
    try:
        from chromadb.api.client import SharedSystemClient

        SharedSystemClient.clear_system_cache()
    except Exception:
        pass
    chroma_dir.mkdir(parents=True, exist_ok=True)

    return {
        "status": "success",
        "deleted_tracked_files": deleted_files,
        "missing_tracked_files": missing_files,
        "inbox_entries_removed": inbox_removed,
        "wiped_dirs": wiped_dirs,
        "source_dir": str(source_dir),
        "database_removed": db_removed,
        "chroma_removed": chroma_removed,
        "message": "Cleared archived files, inbox, metadata database, and RAG index.",
    }
