"""Bind-address policy: env, uvicorn CLI, and in-process Server.config must agree."""

from __future__ import annotations

import asyncio
import gc

import pytest
import uvicorn

from app.main import app
from paperless_agent.local_security import (
    assert_bind_allowed,
    bind_host_from_argv,
    configured_bind_host,
    configured_bind_port,
    effective_bind_host,
    generate_api_token,
    is_wildcard_or_non_loopback_bind,
    sync_configured_bind,
)
from paperless_agent.serve import main as serve_main
from paperless_agent.serve import run as serve_run


def _clear_network_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # setenv (not only delenv) so pytest reverts later os.environ writes such as
    # sync_configured_bind() — otherwise PAPERLESS_HOST leaks into other tests.
    monkeypatch.setenv("PAPERLESS_HOST", "")
    monkeypatch.setenv("PAPERLESS_PORT", "")
    monkeypatch.delenv("PAPERLESS_HOST", raising=False)
    monkeypatch.delenv("PAPERLESS_PORT", raising=False)
    monkeypatch.delenv("PAPERLESS_ALLOW_REMOTE", raising=False)
    monkeypatch.delenv("PAPERLESS_API_TOKEN", raising=False)
    monkeypatch.delenv("PAPERLESS_SSL_CERTFILE", raising=False)
    monkeypatch.delenv("PAPERLESS_SSL_KEYFILE", raising=False)


def test_configured_bind_defaults_and_sync(monkeypatch):
    _clear_network_env(monkeypatch)
    assert configured_bind_host() == "127.0.0.1"
    assert configured_bind_port() == 8080
    monkeypatch.setenv("PAPERLESS_HOST", "localhost")
    monkeypatch.setenv("PAPERLESS_PORT", "9090")
    assert configured_bind_host() == "localhost"
    assert configured_bind_port() == 9090
    monkeypatch.setenv("PAPERLESS_HOST", "0.0.0.0")
    monkeypatch.setenv("PAPERLESS_PORT", "8443")
    sync_configured_bind("0.0.0.0", 8443)
    assert configured_bind_host() == "0.0.0.0"
    assert configured_bind_port() == 8443


def test_loopback_hosts_allowed_without_remote_flags(monkeypatch):
    _clear_network_env(monkeypatch)
    for host in ("127.0.0.1", "localhost", "::1"):
        assert not is_wildcard_or_non_loopback_bind(host)
        assert_bind_allowed(host)


def test_wildcard_and_lan_hosts_require_network_mode(monkeypatch):
    _clear_network_env(monkeypatch)
    for host in ("0.0.0.0", "::", "192.168.1.20", "10.0.0.8"):
        assert is_wildcard_or_non_loopback_bind(host)
        with pytest.raises(RuntimeError, match="network mode"):
            assert_bind_allowed(host)


def test_argv_manual_uvicorn_host(monkeypatch):
    _clear_network_env(monkeypatch)
    monkeypatch.setenv("PAPERLESS_HOST", "127.0.0.1")
    monkeypatch.setattr(
        "paperless_agent.local_security.discover_uvicorn_bind_host",
        lambda: None,
    )
    assert (
        bind_host_from_argv(["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"])
        == "0.0.0.0"
    )
    assert bind_host_from_argv(["uvicorn", "app.main:app", "--host=192.168.0.12"]) == "192.168.0.12"
    monkeypatch.setattr(
        "sys.argv",
        ["uvicorn", "app.main:app", "--host", "0.0.0.0"],
    )
    assert effective_bind_host() == "0.0.0.0"
    with pytest.raises(RuntimeError, match="0.0.0.0"):
        assert_bind_allowed(effective_bind_host())


def test_effective_host_follows_uvicorn_server_not_env(monkeypatch):
    _clear_network_env(monkeypatch)
    monkeypatch.setenv("PAPERLESS_HOST", "127.0.0.1")
    config = uvicorn.Config(app=app, host="0.0.0.0", port=9999, log_level="error")
    server = uvicorn.Server(config)
    try:
        assert effective_bind_host() == "0.0.0.0"
        with pytest.raises(RuntimeError, match="network mode"):
            assert_bind_allowed(effective_bind_host())
    finally:
        server = None  # noqa: F841
        gc.collect()


def test_effective_host_lan_ip_on_uvicorn_server(monkeypatch):
    _clear_network_env(monkeypatch)
    config = uvicorn.Config(app=app, host="10.0.0.5", port=8080, log_level="error")
    server = uvicorn.Server(config)
    try:
        assert effective_bind_host() == "10.0.0.5"
        with pytest.raises(RuntimeError, match="10.0.0.5"):
            assert_bind_allowed(effective_bind_host())
    finally:
        server = None  # noqa: F841
        gc.collect()


async def _uvicorn_startup(server: uvicorn.Server) -> None:
    """Lifespan is attached in Server._serve, not Server.__init__."""
    config = server.config
    if not config.loaded:
        config.load()
    server.lifespan = config.lifespan_class(config)
    await server.startup()


def test_manual_uvicorn_startup_refuses_wildcard(isolated_data, monkeypatch):
    _clear_network_env(monkeypatch)
    config = uvicorn.Config(app=app, host="0.0.0.0", port=0, log_level="error")
    server = uvicorn.Server(config)
    try:
        with pytest.raises(SystemExit) as excinfo:
            asyncio.run(_uvicorn_startup(server))
        assert excinfo.value.code == 3
        assert server.lifespan.startup_failed or server.lifespan.should_exit
    finally:
        server = None  # noqa: F841
        gc.collect()


def test_manual_uvicorn_startup_allows_loopback(isolated_data, monkeypatch):
    _clear_network_env(monkeypatch)
    # Leftover wildcard Server from a prior test must not override this bind.
    _stale = uvicorn.Server(uvicorn.Config(app=app, host="0.0.0.0", port=0, log_level="error"))
    config = uvicorn.Config(app=app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)

    async def _run(srv: uvicorn.Server) -> None:
        await _uvicorn_startup(srv)
        try:
            assert not srv.should_exit
            assert not srv.lifespan.startup_failed
        finally:
            if srv.started:
                await srv.shutdown()
            elif getattr(srv, "lifespan", None):
                await srv.lifespan.shutdown()

    try:
        asyncio.run(_run(server))
    finally:
        _stale = None  # noqa: F841
        server = None  # noqa: F841
        gc.collect()


def test_serve_run_passes_same_host_to_uvicorn(monkeypatch, tmp_path):
    _clear_network_env(monkeypatch)
    captured: dict = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("paperless_agent.serve.uvicorn.run", fake_run)
    serve_run(host="127.0.0.1", port=18080)
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 18080
    assert configured_bind_host() == "127.0.0.1"

    with pytest.raises(RuntimeError, match="network mode"):
        serve_run(host="0.0.0.0", port=18080)

    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("cert", encoding="utf-8")
    key.write_text("key", encoding="utf-8")
    monkeypatch.setenv("PAPERLESS_ALLOW_REMOTE", "1")
    monkeypatch.setenv("PAPERLESS_API_TOKEN", generate_api_token())
    monkeypatch.setenv("PAPERLESS_SSL_CERTFILE", str(cert))
    monkeypatch.setenv("PAPERLESS_SSL_KEYFILE", str(key))
    captured.clear()
    serve_run(host="192.168.1.20", port=8443)
    assert captured["host"] == "192.168.1.20"
    assert captured["ssl_certfile"] == str(cert.resolve())
    assert configured_bind_host() == "192.168.1.20"


def test_serve_main_cli_host(monkeypatch):
    captured: dict = {}

    def fake_run(*, host=None, port=None):
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("paperless_agent.serve.run", fake_run)
    assert serve_main(["--host", "localhost", "--port", "8081"]) == 0
    assert captured == {"host": "localhost", "port": 8081}
