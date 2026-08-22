"""Application-owned uvicorn entry so bind address and bind policy cannot diverge."""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn

from paperless_agent.access_log import install_access_log_redaction
from paperless_agent.local_security import (
    DEFAULT_BIND_HOST,
    assert_bind_allowed,
    configured_bind_host,
    configured_bind_port,
    ssl_cert_paths,
    sync_configured_bind,
)


def run(*, host: str | None = None, port: int | None = None) -> None:
    """Bind uvicorn to ``host``/``port`` after recording them in PAPERLESS_* env."""
    bind_host = (host or configured_bind_host()).strip() or DEFAULT_BIND_HOST
    bind_port = configured_bind_port() if port is None else port
    sync_configured_bind(bind_host, bind_port)
    assert_bind_allowed(bind_host)
    install_access_log_redaction()
    uv_kwargs: dict = {
        "app": "app.main:app",
        "host": bind_host,
        "port": bind_port,
        "log_level": os.getenv("PAPERLESS_LOG_LEVEL", "info"),
    }
    ssl_paths = ssl_cert_paths()
    if ssl_paths:
        cert, key = ssl_paths
        uv_kwargs["ssl_certfile"] = str(cert)
        uv_kwargs["ssl_keyfile"] = str(key)
    uvicorn.run(**uv_kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PaperlessAgent HTTP server")
    parser.add_argument(
        "--host",
        default=None,
        help=f"Bind address (default: PAPERLESS_HOST or {DEFAULT_BIND_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port (default: PAPERLESS_PORT or 8080)",
    )
    args = parser.parse_args(argv)
    run(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
