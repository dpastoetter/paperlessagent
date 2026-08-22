"""FastAPI application entry: lifespan, security middleware, static UI, routers."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.deps import (
    AUTH_EXEMPT_PATHS,
    CSRF_HEADER_NAME,
    CSRF_HEADER_VALUE,
    MAX_UPLOAD_BYTES,
    MUTATING_METHODS,
    peer_host,
    request_has_valid_token,
    request_is_https,
)
from app.routers import build_api_router
from app.security_headers import apply_browser_security_headers
from paperless_agent.config import ensure_data_dirs
from paperless_agent.env_permissions import ensure_dotenv_permissions
from paperless_agent.inbox_worker import inbox_poll_loop
from paperless_agent.local_security import (
    COOKIE_NAME,
    assert_bind_allowed,
    auth_required_for_request,
    get_api_token,
    host_header_allowed,
    is_direct_loopback_request,
)
from paperless_agent.review import recover_stale_processing
from paperless_agent.sessions import (
    attach_session_cookie,
    create_session,
    session_is_valid,
)
from paperless_agent.settings import load_settings
from paperless_agent.version import get_current_version

__all__ = [
    "CSRF_HEADER_NAME",
    "CSRF_HEADER_VALUE",
    "MAX_UPLOAD_BYTES",
    "app",
]

STATIC_DIR = Path(__file__).resolve().parent / "static"
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_data_dirs()
    ensure_dotenv_permissions(fix=True)
    assert_bind_allowed(os.getenv("PAPERLESS_HOST", "127.0.0.1"))
    load_settings()
    recover_stale_processing()
    stop_event = asyncio.Event()
    poller = asyncio.create_task(inbox_poll_loop(stop_event), name="inbox-poller")
    logger.info("Started inbox poller task")
    try:
        yield
    finally:
        stop_event.set()
        poller.cancel()
        try:
            await poller
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="PaperlessAgent",
    description="Local ADK document ingest + RAG query API",
    version=get_current_version(),
    lifespan=lifespan,
)


@app.middleware("http")
async def security_boundary(request: Request, call_next):
    """Host allowlist, optional bearer/session auth, CSRF, and browser headers."""
    path = request.url.path

    if path.startswith("/api/") or path == "/" or path.startswith("/static/"):
        if not host_header_allowed(request.headers.get("host")):
            return apply_browser_security_headers(
                JSONResponse(
                    status_code=400,
                    content={"detail": "invalid Host header"},
                )
            )

    tcp_peer = peer_host(request)
    needs_auth = path.startswith("/api/") and path not in AUTH_EXEMPT_PATHS
    if needs_auth and auth_required_for_request(
        peer_host=tcp_peer,
        host_header=request.headers.get("host"),
    ):
        if not get_api_token():
            return apply_browser_security_headers(
                JSONResponse(
                    status_code=403,
                    content={
                        "detail": (
                            "non-loopback API access requires PAPERLESS_API_TOKEN "
                            "(refuse to expose the unauthenticated API on the network)"
                        )
                    },
                )
            )
        if not request_has_valid_token(request):
            return apply_browser_security_headers(
                JSONResponse(
                    status_code=401,
                    content={
                        "detail": (
                            "authentication required — send Authorization: Bearer "
                            f"<PAPERLESS_API_TOKEN> or create a browser session via "
                            f"POST /api/auth/session (cookie {COOKIE_NAME})"
                        )
                    },
                )
            )

    if (
        request.method in MUTATING_METHODS
        and path.startswith("/api/")
        and request.headers.get(CSRF_HEADER_NAME) != CSRF_HEADER_VALUE
    ):
        return apply_browser_security_headers(
            JSONResponse(
                status_code=403,
                content={
                    "detail": (f"missing {CSRF_HEADER_NAME} header — cross-site request blocked")
                },
            )
        )

    response = await call_next(request)
    if path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return apply_browser_security_headers(response)


app.include_router(build_api_router())


@app.get("/", response_model=None)
def index(request: Request) -> HTMLResponse:
    """
    Serve the SPA.

    Never injects PAPERLESS_API_TOKEN into HTML/JS. Issues a random HttpOnly
    session cookie only for a genuine direct loopback connection (loopback TCP
    peer and loopback Host, not via a trusted proxy). Proxied/public clients
    must use POST /api/auth/session. Query-string tokens are not accepted.
    """
    expected = get_api_token()
    existing = request.cookies.get(COOKIE_NAME)

    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    response = HTMLResponse(html)

    if expected and is_direct_loopback_request(
        peer_host=peer_host(request),
        host_header=request.headers.get("host"),
    ):
        if not session_is_valid(existing):
            attach_session_cookie(response, create_session(), secure=request_is_https(request))
        return response

    return response


@app.get("/settings")
def settings_page() -> RedirectResponse:
    """Settings now lives inside the app shell."""
    return RedirectResponse(url="/#/settings")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
