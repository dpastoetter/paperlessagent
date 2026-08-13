"""Shared path/security helpers for API routers."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request

from paperless_agent.config import ARCHIVE_DIR
from paperless_agent.local_security import (
    AUTH_HEADER,
    COOKIE_NAME,
    extract_bearer_token,
    get_api_token,
    token_matches,
)
from paperless_agent.privacy import require_cloud_disclaimer
from paperless_agent.settings import load_settings

# CSRF: browsers cannot attach custom headers to cross-site form posts without
# CORS preflight (which this app never grants).
CSRF_HEADER_NAME = "X-Requested-With"
CSRF_HEADER_VALUE = "PaperlessAgent"
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
AUTH_EXEMPT_PATHS = frozenset({"/api/health"})

MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # generous cap for large scans


def client_host(request: Request) -> str | None:
    if request.client is None:
        return None
    return request.client.host


def request_has_valid_token(request: Request) -> bool:
    expected = get_api_token()
    if not expected:
        return False
    bearer = extract_bearer_token(request.headers.get(AUTH_HEADER))
    if token_matches(bearer, expected):
        return True
    cookie = request.cookies.get(COOKIE_NAME)
    return token_matches(cookie, expected)


def is_within(path: Path, root: Path) -> bool:
    try:
        return path.is_relative_to(root)
    except (OSError, ValueError):
        return False


def archive_roots() -> list[Path]:
    """Directories archived documents are allowed to be served from."""
    roots = [Path(ARCHIVE_DIR).expanduser().resolve()]
    for category in load_settings().get("categories", []):
        folder = category.get("folder")
        if folder:
            roots.append(Path(folder).expanduser().resolve())
    return roots


def require_cloud_disclaimer_or_403() -> None:
    try:
        require_cloud_disclaimer()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
