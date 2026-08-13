"""FastAPI application entry: lifespan, security middleware, static UI, routers."""

from __future__ import annotations

import asyncio
import json
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
)
from app.routers import build_api_router
from paperless_agent.config import ensure_data_dirs
from paperless_agent.inbox_worker import inbox_poll_loop
from paperless_agent.local_security import (
    COOKIE_NAME,
    assert_bind_allowed,
    auth_required_for_request,
    get_api_token,
    host_header_allowed,
    is_loopback_hostname,
    token_matches,
)
from paperless_agent.review import recover_stale_processing
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
    """Host allowlist, optional bearer/session auth, and CSRF on mutations."""
    path = request.url.path

    if path.startswith("/api/") or path == "/" or path.startswith("/static/"):
        if not host_header_allowed(request.headers.get("host")):
            return JSONResponse(
                status_code=400,
                content={"detail": "invalid Host header"},
            )

    peer = client_host(request)
    needs_auth = path.startswith("/api/") and path not in AUTH_EXEMPT_PATHS
    if needs_auth and auth_required_for_request(client_host=peer):
        if not get_api_token():
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        "non-loopback API access requires PAPERLESS_API_TOKEN "
                        "(refuse to expose the unauthenticated API on the network)"
                    )
                },
            )
        if not request_has_valid_token(request):
            return JSONResponse(
                status_code=401,
                content={
                    "detail": (
                        "authentication required — send Authorization: Bearer "
                        f"<token> or open the UI once with ?token=… "
                        f"(cookie {COOKIE_NAME})"
                    )
                },
            )

    if (
        request.method in MUTATING_METHODS
        and path.startswith("/api/")
        and request.headers.get(CSRF_HEADER_NAME) != CSRF_HEADER_VALUE
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": (f"missing {CSRF_HEADER_NAME} header — cross-site request blocked")},
        )

    response = await call_next(request)
    if path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


app.include_router(build_api_router())


@app.get("/", response_model=None)
def index(request: Request) -> HTMLResponse | RedirectResponse:
    """Serve the SPA; bootstrap API token for loopback / ?token= sessions."""
    expected = get_api_token()
    query_token = request.query_params.get("token")

    if expected and token_matches(query_token, expected):
        redirect = RedirectResponse(url="/", status_code=303)
        redirect.set_cookie(
            key=COOKIE_NAME,
            value=expected,
            httponly=True,
            samesite="strict",
            path="/",
            max_age=60 * 60 * 24 * 365,
        )
        return redirect

    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    if expected and is_loopback_hostname(client_host(request)):
        snippet = "<script>window.PA_API_TOKEN=" + json.dumps(expected) + ";</script>\n</head>"
        if "</head>" in html:
            html = html.replace("</head>", snippet, 1)
        else:
            html = snippet + html
        response = HTMLResponse(html)
        response.set_cookie(
            key=COOKIE_NAME,
            value=expected,
            httponly=True,
            samesite="strict",
            path="/",
            max_age=60 * 60 * 24 * 365,
        )
        return response

    return HTMLResponse(html)


@app.get("/settings")
def settings_page() -> RedirectResponse:
    """Settings now lives inside the app shell."""
    return RedirectResponse(url="/#/settings")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
