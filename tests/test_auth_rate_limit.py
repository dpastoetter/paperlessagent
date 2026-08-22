"""Per-IP authentication rate limits."""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from app.main import CSRF_HEADER_NAME, CSRF_HEADER_VALUE, app
from deepcatalog import auth_rate_limit as arl
from deepcatalog.auth_rate_limit import (
    AUTH_FAILURE_LIMIT,
    RATE_LIMIT_DETAIL,
    SESSION_ATTEMPT_LIMIT,
    SESSION_FAILURE_LIMIT,
    AuthRateLimiter,
)
from deepcatalog.local_security import generate_api_token
from deepcatalog.sessions import clear_all_sessions


class _Clock:
    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_session_limiter_allows_normal_retries():
    clock = _Clock()
    limiter = AuthRateLimiter(now=clock)
    ip = "203.0.113.10"
    for _ in range(4):
        ok, _retry = limiter.check_session(ip)
        assert ok
        limiter.record_session_failure(ip)
    ok, _retry = limiter.check_session(ip)
    assert ok
    limiter.record_session_success(ip)
    ok, _retry = limiter.check_session(ip)
    assert ok


def test_session_limiter_lockout_after_failures():
    clock = _Clock()
    limiter = AuthRateLimiter(now=clock)
    ip = "198.51.100.7"
    for _ in range(SESSION_FAILURE_LIMIT):
        assert limiter.check_session(ip)[0]
        limiter.record_session_failure(ip)
    allowed, retry = limiter.check_session(ip)
    assert allowed is False
    assert retry >= 1
    clock.advance(10)
    assert limiter.check_session(ip)[0] is False


def test_session_limiter_is_per_ip():
    clock = _Clock()
    limiter = AuthRateLimiter(now=clock)
    attacker = "203.0.113.1"
    neighbor = "203.0.113.2"
    for _ in range(SESSION_FAILURE_LIMIT):
        limiter.check_session(attacker)
        limiter.record_session_failure(attacker)
    assert limiter.check_session(attacker)[0] is False
    assert limiter.check_session(neighbor)[0] is True


def test_session_attempt_cap_without_failures():
    clock = _Clock()
    limiter = AuthRateLimiter(now=clock)
    ip = "192.0.2.9"
    for _ in range(SESSION_ATTEMPT_LIMIT):
        assert limiter.check_session(ip)[0]
        limiter.record_session_success(ip)
    allowed, retry = limiter.check_session(ip)
    assert allowed is False
    assert retry >= 1


def test_api_auth_failures_lock_then_expire():
    clock = _Clock()
    limiter = AuthRateLimiter(now=clock)
    ip = "203.0.113.50"
    for _ in range(AUTH_FAILURE_LIMIT):
        assert limiter.check_api_auth(ip)[0]
        limiter.record_api_auth_failure(ip, "/api/inbox")
    allowed, retry = limiter.check_api_auth(ip)
    assert allowed is False
    assert retry >= 1
    clock.advance(10_000)
    assert limiter.check_api_auth(ip)[0] is True


def test_auth_failure_logs_do_not_include_secrets(caplog):
    caplog.set_level(logging.WARNING, logger="deepcatalog.auth_rate_limit")
    limiter = AuthRateLimiter()
    secret = "super-secret-api-token-value"
    limiter.record_session_failure("10.0.0.9")
    limiter.record_api_auth_failure("10.0.0.9", "/api/inbox")
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert secret not in joined
    assert "token=" not in joined
    assert "Authorization" not in joined
    assert "ip=10.0.0.9" in joined
    assert "Authentication failed" in joined


def test_session_http_rate_limit(isolated_data, monkeypatch):
    token = generate_api_token()
    monkeypatch.setenv("DEEPCATALOG_API_TOKEN", token)
    monkeypatch.setattr(arl, "SESSION_FAILURE_LIMIT", 3)
    clear_all_sessions()
    client = TestClient(app)
    client.headers.update({CSRF_HEADER_NAME: CSRF_HEADER_VALUE})
    for _ in range(3):
        resp = client.post("/api/auth/session", json={"token": "wrong-token-value"})
        assert resp.status_code == 401
    locked = client.post("/api/auth/session", json={"token": "wrong-token-value"})
    assert locked.status_code == 429
    assert RATE_LIMIT_DETAIL in locked.json()["detail"]
    assert locked.headers.get("Retry-After")
    assert client.post("/api/auth/session", json={"token": token}).status_code == 429


def test_api_401_rate_limit_is_per_ip(isolated_data, monkeypatch):
    token = generate_api_token()
    monkeypatch.setenv("DEEPCATALOG_API_TOKEN", token)
    monkeypatch.setattr(arl, "AUTH_FAILURE_LIMIT", 3)
    clear_all_sessions()
    client = TestClient(app)
    for _ in range(3):
        assert client.get("/api/inbox").status_code == 401
    blocked = client.get("/api/inbox")
    assert blocked.status_code == 429
    assert blocked.headers.get("Retry-After")
    assert client.get("/api/inbox", headers={"Authorization": f"Bearer {token}"}).status_code == 429
    other = TestClient(app, client=("203.0.113.80", 50000), base_url="https://testserver")
    other_ok = other.get(
        "/api/inbox",
        headers={"Authorization": f"Bearer {token}", "Host": "testserver"},
    )
    assert other_ok.status_code == 200


def test_valid_api_usage_is_not_rate_limited(isolated_data, monkeypatch):
    token = generate_api_token()
    monkeypatch.setenv("DEEPCATALOG_API_TOKEN", token)
    monkeypatch.setattr(arl, "AUTH_FAILURE_LIMIT", 3)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    for _ in range(8):
        assert client.get("/api/inbox", headers=headers).status_code == 200
