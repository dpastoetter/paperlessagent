"""Tests for the desktop shell helpers (no GUI)."""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from paperless_agent.desktop import (
    _pick_port,
    health_url,
    is_server_healthy,
    wait_for_health,
)


def test_pick_port_returns_preferred():
    assert _pick_port("127.0.0.1", 9090) == 9090


def test_pick_port_returns_positive():
    port = _pick_port("127.0.0.1", None)
    assert isinstance(port, int)
    assert port > 0


def test_wait_for_health_succeeds():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        wait_for_health("127.0.0.1", port, timeout=5.0)
        assert is_server_healthy("127.0.0.1", port)
        assert health_url("127.0.0.1", port).endswith(f":{port}/api/health")
    finally:
        server.shutdown()


def test_wait_for_health_times_out():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    # Port is not listening after the socket closes — health must time out.
    with pytest.raises(TimeoutError, match="did not become ready"):
        wait_for_health("127.0.0.1", port, timeout=0.4)
