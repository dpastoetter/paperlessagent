"""API security boundary: bind policy, bearer token, session cookies."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import CSRF_HEADER_NAME, CSRF_HEADER_VALUE, app
from paperless_agent.local_security import (
    COOKIE_NAME,
    assert_bind_allowed,
    forwarded_client_host,
    generate_api_token,
    is_direct_loopback_request,
    is_loopback_hostname,
    is_wildcard_or_non_loopback_bind,
    request_appears_https,
)
from paperless_agent.sessions import clear_all_sessions, session_is_valid


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


def test_assert_bind_requires_remote_opt_in_token_and_tls(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPERLESS_API_TOKEN", raising=False)
    monkeypatch.delenv("PAPERLESS_ALLOW_REMOTE", raising=False)
    monkeypatch.delenv("PAPERLESS_SSL_CERTFILE", raising=False)
    monkeypatch.delenv("PAPERLESS_SSL_KEYFILE", raising=False)

    with pytest.raises(RuntimeError, match="PAPERLESS_ALLOW_REMOTE"):
        assert_bind_allowed("0.0.0.0")

    monkeypatch.setenv("PAPERLESS_ALLOW_REMOTE", "1")
    with pytest.raises(RuntimeError, match="PAPERLESS_API_TOKEN"):
        assert_bind_allowed("0.0.0.0")

    monkeypatch.setenv("PAPERLESS_API_TOKEN", generate_api_token())
    with pytest.raises(RuntimeError, match="PAPERLESS_SSL_CERTFILE"):
        assert_bind_allowed("0.0.0.0")

    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("cert", encoding="utf-8")
    key.write_text("key", encoding="utf-8")
    monkeypatch.setenv("PAPERLESS_SSL_CERTFILE", str(cert))
    monkeypatch.setenv("PAPERLESS_SSL_KEYFILE", str(key))
    assert_bind_allowed("0.0.0.0")  # network mode fully configured

    # Loopback stays local mode (HTTP OK, no remote flags needed).
    monkeypatch.delenv("PAPERLESS_ALLOW_REMOTE", raising=False)
    monkeypatch.delenv("PAPERLESS_API_TOKEN", raising=False)
    monkeypatch.delenv("PAPERLESS_SSL_CERTFILE", raising=False)
    monkeypatch.delenv("PAPERLESS_SSL_KEYFILE", raising=False)
    assert_bind_allowed("127.0.0.1")


def test_invalid_host_header_rejected(isolated_data):
    client = TestClient(app)
    resp = client.get("/api/health", headers={"Host": "evil.example"})
    assert resp.status_code == 400
    assert "Host" in resp.json()["detail"]


def test_token_required_when_configured(isolated_data, monkeypatch):
    token = generate_api_token()
    monkeypatch.setenv("PAPERLESS_API_TOKEN", token)
    clear_all_sessions()
    bare = TestClient(app)
    denied = bare.get("/api/inbox")
    assert denied.status_code == 401

    ok = bare.get(
        "/api/inbox",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ok.status_code == 200

    # API secret must not work as a cookie value anymore.
    bare.cookies.set(COOKIE_NAME, token)
    assert bare.get("/api/inbox").status_code == 401


def test_session_exchange_sets_independent_cookie(isolated_data, monkeypatch):
    token = generate_api_token()
    monkeypatch.setenv("PAPERLESS_API_TOKEN", token)
    clear_all_sessions()
    client = TestClient(app)
    client.headers.update({CSRF_HEADER_NAME: CSRF_HEADER_VALUE})

    bad = client.post("/api/auth/session", json={"token": "wrong-token-value"})
    assert bad.status_code == 401

    exchanged = client.post("/api/auth/session", json={"token": token})
    assert exchanged.status_code == 200
    session = exchanged.cookies.get(COOKIE_NAME)
    assert session
    assert session != token
    assert session_is_valid(session)

    # Cookie authenticates subsequent API calls without Bearer.
    authed = TestClient(app)
    authed.cookies.set(COOKIE_NAME, session)
    assert authed.get("/api/inbox").status_code == 200

    # Logout revokes the hashed session.
    authed.headers.update({CSRF_HEADER_NAME: CSRF_HEADER_VALUE})
    logged_out = authed.post("/api/auth/session/logout")
    assert logged_out.status_code == 200
    assert not session_is_valid(session)


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
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "ok", "version": body["version"]}
    assert "auth" not in body
    assert "usage" not in body
    assert "llm_provider" not in body


def test_diagnostics_requires_bearer_when_token_set(isolated_data, monkeypatch):
    token = generate_api_token()
    monkeypatch.setenv("PAPERLESS_API_TOKEN", token)
    client = TestClient(app)
    assert client.get("/api/diagnostics").status_code == 401
    ok = client.get("/api/diagnostics", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["status"] in {"ok", "degraded"}
    assert "usage" in body
    assert "auth" in body
    assert "llm_provider" in body


def test_index_sets_random_session_cookie_on_loopback(isolated_data, monkeypatch):
    token = generate_api_token()
    monkeypatch.setenv("PAPERLESS_API_TOKEN", token)
    clear_all_sessions()
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert COOKIE_NAME in resp.cookies
    cookie = resp.cookies.get(COOKIE_NAME)
    assert cookie
    assert cookie != token
    assert "PA_API_TOKEN" not in resp.text
    assert session_is_valid(cookie)


def test_query_token_is_ignored(isolated_data, monkeypatch):
    token = generate_api_token()
    monkeypatch.setenv("PAPERLESS_API_TOKEN", token)
    monkeypatch.setenv("PAPERLESS_ALLOWED_HOSTS", "paperless.example.com")
    clear_all_sessions()
    # Non-loopback peer so localhost auto-login cannot mask a query exchange.
    client = TestClient(app, client=("203.0.113.9", 50000))
    headers = {"Host": "paperless.example.com"}
    resp = client.get(f"/?token={token}", headers=headers, follow_redirects=False)
    assert resp.status_code == 200
    assert resp.headers.get("location") is None
    assert not resp.cookies.get(COOKIE_NAME)
    assert client.get("/api/inbox", headers=headers).status_code == 401


def test_forwarded_headers_only_from_trusted_proxies(monkeypatch):
    monkeypatch.setenv("PAPERLESS_TRUSTED_PROXIES", "10.0.0.1,192.168.0.0/24")
    # Untrusted peer — ignore spoofed XFF.
    assert (
        forwarded_client_host(
            peer_host="8.8.8.8",
            x_forwarded_for="1.2.3.4",
        )
        == "8.8.8.8"
    )
    # Trusted peer — walk right-to-left, skip trusted hops, take the client.
    assert (
        forwarded_client_host(
            peer_host="10.0.0.1",
            x_forwarded_for="203.0.113.9, 10.0.0.1",
        )
        == "203.0.113.9"
    )
    assert not request_appears_https(
        peer_host="8.8.8.8",
        url_scheme="http",
        x_forwarded_proto="https",
    )
    assert request_appears_https(
        peer_host="10.0.0.1",
        url_scheme="http",
        x_forwarded_proto="https",
    )
    # Spoofed loopback on the left must not win over the real client on the right.
    monkeypatch.setenv("PAPERLESS_TRUSTED_PROXIES", "127.0.0.1")
    assert (
        forwarded_client_host(
            peer_host="127.0.0.1",
            x_forwarded_for="127.0.0.2, 203.0.113.9",
        )
        == "203.0.113.9"
    )


def test_direct_loopback_request_ignores_forwarded_headers(monkeypatch):
    monkeypatch.delenv("PAPERLESS_TRUSTED_PROXIES", raising=False)
    assert is_direct_loopback_request(peer_host="127.0.0.1", host_header="localhost")
    assert is_direct_loopback_request(peer_host="testclient", host_header="testserver")
    assert not is_direct_loopback_request(
        peer_host="127.0.0.1", host_header="paperless.example.com"
    )
    assert not is_direct_loopback_request(peer_host="203.0.113.9", host_header="localhost")

    monkeypatch.setenv("PAPERLESS_TRUSTED_PROXIES", "127.0.0.1,::1")
    # nginx on loopback is a trusted hop — never treat it as the user.
    assert not is_direct_loopback_request(peer_host="127.0.0.1", host_header="localhost")
    assert not is_direct_loopback_request(
        peer_host="127.0.0.1", host_header="paperless.example.com"
    )


def test_spoofed_xff_does_not_issue_session_behind_proxy(isolated_data, monkeypatch):
    token = generate_api_token()
    monkeypatch.setenv("PAPERLESS_API_TOKEN", token)
    monkeypatch.setenv("PAPERLESS_TRUSTED_PROXIES", "127.0.0.1")
    monkeypatch.setenv("PAPERLESS_ALLOWED_HOSTS", "paperless.example.com")
    clear_all_sessions()
    client = TestClient(app, client=("127.0.0.1", 50000))
    spoof = {
        "Host": "paperless.example.com",
        "X-Forwarded-For": "127.0.0.2",
    }
    resp = client.get("/", headers=spoof)
    assert resp.status_code == 200
    assert not resp.cookies.get(COOKIE_NAME)
    assert client.get("/api/inbox", headers=spoof).status_code == 401

    # Host: localhost is allowlisted but still must not auto-login via the proxy.
    local_host = {"Host": "localhost", "X-Forwarded-For": "127.0.0.2"}
    resp = client.get("/", headers=local_host)
    assert not resp.cookies.get(COOKIE_NAME)
    assert client.get("/api/inbox", headers=local_host).status_code == 401


def test_spoofed_xff_does_not_skip_auth_without_token(isolated_data, monkeypatch):
    monkeypatch.delenv("PAPERLESS_API_TOKEN", raising=False)
    monkeypatch.setenv("PAPERLESS_TRUSTED_PROXIES", "127.0.0.1")
    monkeypatch.setenv("PAPERLESS_ALLOWED_HOSTS", "paperless.example.com")
    client = TestClient(app, client=("127.0.0.1", 50000))
    resp = client.get(
        "/api/inbox",
        headers={"Host": "paperless.example.com", "X-Forwarded-For": "127.0.0.2"},
    )
    assert resp.status_code == 403
    assert "PAPERLESS_API_TOKEN" in resp.json()["detail"]
