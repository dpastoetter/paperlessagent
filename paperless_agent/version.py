"""Installed package version — single resolution path for app, updater, and OpenAPI."""

from __future__ import annotations

import re
from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path

from paperless_agent import config

_PACKAGE_NAME = "paperlessagent"
_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', flags=re.MULTILINE)


@lru_cache(maxsize=1)
def get_current_version() -> str:
    """
    Return the installed PaperlessAgent version.

    Prefer distribution metadata (``pip show`` / wheel). Fall back to reading
    ``pyproject.toml`` only when running from a source tree that is not
    installed as a distribution.
    """
    try:
        return package_version(_PACKAGE_NAME)
    except PackageNotFoundError:
        pass

    pyproject = Path(config.PROJECT_ROOT) / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return "0.0.0"
    match = _VERSION_RE.search(text)
    return match.group(1) if match else "0.0.0"


def clear_version_cache() -> None:
    """Drop cached version (tests / after an in-place update before restart)."""
    get_current_version.cache_clear()


def __getattr__(name: str) -> str:
    if name == "__version__":
        return get_current_version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
