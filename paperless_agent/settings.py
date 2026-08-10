"""User-editable setup: source folder, categories, and batch parameters."""

from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from paperless_agent import config

SETTINGS_FILENAME = "settings.json"

_lock = threading.RLock()
_cache: dict[str, Any] | None = None


def settings_path() -> Path:
    return config.DATA_DIR / SETTINGS_FILENAME


def default_settings() -> dict[str, Any]:
    """Seed settings from current DATA_DIR layout and DOC_TYPES."""
    return {
        "source_dir": str(config.INBOX_DIR.resolve()),
        "categories": [
            {"name": name, "folder": str((config.ARCHIVE_DIR / name).resolve())}
            for name in config.DOC_TYPES
        ],
        "batch": {
            # How often (seconds) to scan the inbox for new files. 0 = manual only.
            "poll_interval_seconds": 30.0,
        },
        "review": {
            # Hold every proposed filing for human approval before writing.
            "require_approval": True,
        },
    }


def _normalize_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _normalize_category_name(name: str) -> str:
    cleaned = (name or "").strip().lower()
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in cleaned)
    cleaned = cleaned.strip("_-")
    return cleaned or "other"


def validate_path(path: str) -> dict[str, Any]:
    """Return existence / directory info for a path string (for UI feedback)."""
    try:
        resolved = _normalize_path(path)
    except OSError as exc:
        return {
            "status": "error",
            "path": path,
            "error": str(exc),
            "exists": False,
            "is_dir": False,
        }
    return {
        "status": "success",
        "path": str(resolved),
        "exists": resolved.exists(),
        "is_dir": resolved.is_dir(),
    }


def validate_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize and validate a settings payload.

    Raises ValueError with a human-readable message on invalid input.
    """
    if not isinstance(payload, dict):
        raise ValueError("settings must be an object")

    source_raw = payload.get("source_dir")
    if not isinstance(source_raw, str) or not source_raw.strip():
        raise ValueError("source_dir is required")
    source_dir = _normalize_path(source_raw)

    categories_raw = payload.get("categories")
    if not isinstance(categories_raw, list) or not categories_raw:
        raise ValueError("categories must be a non-empty list")

    categories: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in categories_raw:
        if not isinstance(item, dict):
            raise ValueError("each category must be an object with name and folder")
        name = _normalize_category_name(str(item.get("name") or ""))
        folder_raw = item.get("folder")
        if not isinstance(folder_raw, str) or not folder_raw.strip():
            raise ValueError(f"category '{name}' needs a folder path")
        folder = _normalize_path(folder_raw)
        if name in seen:
            raise ValueError(f"duplicate category name: {name}")
        seen.add(name)
        categories.append({"name": name, "folder": str(folder)})

    if "other" not in seen:
        raise ValueError("categories must include an 'other' entry")

    batch_raw = payload.get("batch") or {}
    if not isinstance(batch_raw, dict):
        raise ValueError("batch must be an object")

    # Prefer poll_interval_seconds; migrate older delay_seconds if present.
    raw_interval = batch_raw.get("poll_interval_seconds", batch_raw.get("delay_seconds", 30))
    try:
        poll_interval_seconds = float(raw_interval)
    except (TypeError, ValueError) as exc:
        raise ValueError("batch.poll_interval_seconds must be a number") from exc
    if poll_interval_seconds < 0:
        raise ValueError("batch.poll_interval_seconds must be >= 0")

    review_raw = payload.get("review") or {}
    if not isinstance(review_raw, dict):
        raise ValueError("review must be an object")
    require_approval = bool(review_raw.get("require_approval", True))

    return {
        "source_dir": str(source_dir),
        "categories": categories,
        "batch": {
            "poll_interval_seconds": poll_interval_seconds,
        },
        "review": {
            "require_approval": require_approval,
        },
    }


def ensure_settings_dirs(settings: dict[str, Any]) -> None:
    """Create source and category folders from validated settings."""
    Path(settings["source_dir"]).mkdir(parents=True, exist_ok=True)
    for cat in settings["categories"]:
        Path(cat["folder"]).mkdir(parents=True, exist_ok=True)


def _read_file() -> dict[str, Any] | None:
    path = settings_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _write_file(settings: dict[str, Any]) -> None:
    config.ensure_data_dirs()
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_settings(*, reload: bool = False) -> dict[str, Any]:
    """Load settings from disk (cached), creating defaults if missing/invalid."""
    global _cache
    with _lock:
        if _cache is not None and not reload:
            return deepcopy(_cache)

        raw = _read_file()
        if raw is None:
            settings = default_settings()
            ensure_settings_dirs(settings)
            _write_file(settings)
            _cache = settings
            return deepcopy(_cache)

        try:
            settings = validate_settings(raw)
        except ValueError:
            settings = default_settings()
            ensure_settings_dirs(settings)
            _write_file(settings)

        ensure_settings_dirs(settings)
        _cache = settings
        return deepcopy(_cache)


def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate, persist, and cache settings. Returns the saved dict."""
    global _cache
    settings = validate_settings(payload)
    ensure_settings_dirs(settings)
    with _lock:
        _write_file(settings)
        _cache = settings
        return deepcopy(_cache)


def clear_settings_cache() -> None:
    """Drop in-memory cache (tests / after external edits)."""
    global _cache
    with _lock:
        _cache = None


def get_source_dir() -> Path:
    return Path(load_settings()["source_dir"])


def get_category_names() -> tuple[str, ...]:
    return tuple(c["name"] for c in load_settings()["categories"])


def get_folder_for_category(name: str) -> Path:
    """Resolve archive folder for a category; fall back to 'other' then ARCHIVE_DIR/other."""
    settings = load_settings()
    key = _normalize_category_name(name)
    by_name = {c["name"]: Path(c["folder"]) for c in settings["categories"]}
    if key in by_name:
        return by_name[key]
    if "other" in by_name:
        return by_name["other"]
    return config.ARCHIVE_DIR / "other"


def get_batch_settings() -> dict[str, Any]:
    return deepcopy(load_settings()["batch"])


def review_approval_required() -> bool:
    """Whether proposed filings must be approved by a human before writing."""
    return bool(load_settings().get("review", {}).get("require_approval", True))
