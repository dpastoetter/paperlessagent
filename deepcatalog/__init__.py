"""DeepCatalog: local ADK pipeline for document ingest and filing."""

from __future__ import annotations

from typing import Any

from deepcatalog.version import get_current_version

__all__ = ["__version__", "get_current_version"]


def __getattr__(name: str) -> Any:
    if name == "__version__":
        return get_current_version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
