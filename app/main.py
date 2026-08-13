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
    client_host,
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
    is_loopback_hostname,
)
from paperless_agent.review import recover_stale_processing
from paperless_agent.sessions import (
    attach_session_cookie,
    create_session,
    exchange_api_token,
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

    peer = client_host(request)
    needs_auth = path.startswith("/api/") and path not in AUTH_EXEMPT_PATHS
    if needs_auth and auth_required_for_request(client_host=peer):
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
def index(request: Request) -> HTMLResponse | RedirectResponse:
    """
    Serve the SPA.

    Never injects PAPERLESS_API_TOKEN into HTML/JS. Issues a random HttpOnly
    session cookie for loopback peers, or exchanges a one-time ?token= query
    into a session and redirects (prefer POST /api/auth/session instead).
    """
    expected = get_api_token()
    query_token = request.query_params.get("token")
    existing = request.cookies.get(COOKIE_NAME)

    # Prefer POST /api/auth/session; ?token= remains a one-shot exchange that
    # never puts the long-lived secret into the cookie or page body.
    if expected and query_token:
        raw = exchange_api_token(query_token)
        if raw:
            redirect = RedirectResponse(url="/", status_code=303)
            attach_session_cookie(redirect, raw, secure=request_is_https(request))
            return redirect

    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    response = HTMLResponse(html)

    if expected and is_loopback_hostname(client_host(request)):
        if not session_is_valid(existing):
            attach_session_cookie(response, create_session(), secure=request_is_https(request))
        return response

    return response


@app.get("/settings")
def settings_page() -> RedirectResponse:
    """Settings now lives inside the app shell."""
    return RedirectResponse(url="/#/settings")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
