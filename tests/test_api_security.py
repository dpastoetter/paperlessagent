"""API security boundary: bind policy, bearer token, Host allowlist."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import CSRF_HEADER_NAME, CSRF_HEADER_VALUE, app
from paperless_agent.local_security import (
    COOKIE_NAME,
    assert_bind_allowed,
    generate_api_token,
    is_loopback_hostname,
    is_wildcard_or_non_loopback_bind,
)


def test_loopback_bind_helpers():
    assert is_loopback_hostname("127.0.0.1")
    assert is_loopback_hostname("localhost:8080")
    assert is_loopback_hostname("::1")
    assert is_loopback_hostname("testclient")
    assert not is_loopback_hostname("192.168.1.10")
    assert is_wildcard_or_non_loopback_bind("0.0.0.0")
    assert is_wildcard_or_non_loopback_bind("::")
    assert is_wildcard_or_non_loopback_bind("192.168.0.5")
    assert not is_wildcard_or_non_loopback_bind("127.0.0.1")


def test_assert_bind_requires_token_for_non_loopback(monkeypatch):
    monkeypatch.delenv("PAPERLESS_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="PAPERLESS_API_TOKEN"):
        assert_bind_allowed("0.0.0.0")
    monkeypatch.setenv("PAPERLESS_API_TOKEN", generate_api_token())
    assert_bind_allowed("0.0.0.0")  # does not raise


def test_invalid_host_header_rejected(isolated_data):
    client = TestClient(app)
    resp = client.get("/api/health", headers={"Host": "evil.example"})
    assert resp.status_code == 400
    assert "Host" in resp.json()["detail"]


def test_token_required_when_configured(isolated_data, monkeypatch):
    token = generate_api_token()
    monkeypatch.setenv("PAPERLESS_API_TOKEN", token)
    bare = TestClient(app)
    denied = bare.get("/api/inbox")
    assert denied.status_code == 401

    ok = bare.get(
        "/api/inbox",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ok.status_code == 200

    # Cookie session also works (SSE / browser).
    bare.cookies.set(COOKIE_NAME, token)
    assert bare.get("/api/inbox").status_code == 200


def test_mutations_still_need_csrf_with_token(isolated_data, monkeypatch):
    token = generate_api_token()
    monkeypatch.setenv("PAPERLESS_API_TOKEN", token)
    client = TestClient(app)
    resp = client.post(
        "/api/process-inbox",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert "cross-site" in resp.json()["detail"]

    resp = client.post(
        "/api/process-inbox",
        headers={
            "Authorization": f"Bearer {token}",
            CSRF_HEADER_NAME: CSRF_HEADER_VALUE,
        },
    )
    assert resp.status_code == 200


def test_health_exempt_from_bearer(isolated_data, monkeypatch):
    monkeypatch.setenv("PAPERLESS_API_TOKEN", generate_api_token())
    client = TestClient(app)
    assert client.get("/api/health").status_code == 200


def test_index_sets_session_cookie_on_loopback_when_token_configured(isolated_data, monkeypatch):
    token = generate_api_token()
    monkeypatch.setenv("PAPERLESS_API_TOKEN", token)
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert COOKIE_NAME in resp.cookies
    assert resp.cookies.get(COOKIE_NAME) == token
    assert "PA_API_TOKEN" in resp.text
