"""Restrictive permissions for secret-bearing files (``.env``, mirrors OAuth 0600)."""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path
from typing import Any

from deepcatalog import config

logger = logging.getLogger(__name__)

# Owner read/write only — same posture as ~/.codex/auth.json.
SECRET_FILE_MODE = 0o600
# Any group/other read/write/execute bit is too open for secrets.
_INSECURE_BITS = stat.S_IRWXG | stat.S_IRWXO


def is_group_or_world_accessible(path: Path) -> bool:
    """True when group or other have any permission bits set."""
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return bool(mode & _INSECURE_BITS)


def chmod_secret_file(path: Path) -> None:
    """Best-effort ``chmod 0600`` (no-op / ignored where unsupported)."""
    try:
        os.chmod(path, SECRET_FILE_MODE)
    except OSError as exc:
        logger.warning("Could not set mode %04o on %s: %s", SECRET_FILE_MODE, path, exc)


def write_secret_text(path: Path, text: str) -> None:
    """
    Atomically write ``text`` to ``path`` with mode ``0600``.

    Creates the temp file with restrictive permissions so a crash never leaves
    a world-readable secrets file behind.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    # On Windows, the mode arg is masked; still set 0600 for POSIX.
    fd = os.open(str(tmp), flags, SECRET_FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            if text and not text.endswith("\n"):
                handle.write("\n")
        # Re-assert in case umask widened the mode.
        chmod_secret_file(tmp)
        os.replace(str(tmp), str(path))
        chmod_secret_file(path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def harden_secret_file(path: Path, *, fix: bool = True) -> dict[str, Any]:
    """
    Inspect ``path`` and optionally tighten permissions to ``0600``.

    Returns a status dict; never raises for permission probing failures.
    """
    target = Path(path)
    result: dict[str, Any] = {
        "path": str(target),
        "exists": target.is_file(),
        "was_insecure": False,
        "fixed": False,
        "mode": None,
    }
    if not target.is_file():
        return result
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
    except OSError as exc:
        result["error"] = str(exc)
        return result
    result["mode"] = oct(mode)
    insecure = bool(mode & _INSECURE_BITS)
    result["was_insecure"] = insecure
    if insecure and fix:
        chmod_secret_file(target)
        try:
            new_mode = stat.S_IMODE(target.stat().st_mode)
            result["mode"] = oct(new_mode)
            result["fixed"] = not bool(new_mode & _INSECURE_BITS)
        except OSError as exc:
            result["error"] = str(exc)
    return result


def candidate_env_paths() -> list[Path]:
    """Project and data-dir ``.env`` locations that may hold secrets."""
    paths = [config.PROJECT_ROOT / ".env", Path(config.DATA_DIR) / ".env"]
    # Preserve order, drop duplicates (e.g. DATA_DIR under PROJECT_ROOT).
    seen: set[Path] = set()
    out: list[Path] = []
    for path in paths:
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def ensure_dotenv_permissions(*, fix: bool = True) -> list[dict[str, Any]]:
    """
    Check known ``.env`` files at startup; tighten and warn if world/group-readable.
    """
    reports: list[dict[str, Any]] = []
    for path in candidate_env_paths():
        report = harden_secret_file(path, fix=fix)
        reports.append(report)
        if report.get("was_insecure"):
            if report.get("fixed"):
                logger.warning(
                    "Tightened permissions on %s to 0600 (was group/world-accessible)",
                    report["path"],
                )
            else:
                logger.warning(
                    "Secret file %s is group/world-accessible (%s); "
                    "could not auto-fix — run: chmod 600 %s",
                    report["path"],
                    report.get("mode"),
                    report["path"],
                )
    return reports
