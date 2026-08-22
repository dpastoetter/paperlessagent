"""Tests for Codex OAuth helpers (no live OpenAI calls)."""

from __future__ import annotations

import base64
import json

from deepcatalog import codex_oauth


def _fake_jwt(account_id: str = "acct_123", email: str = "user@example.com") -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = {
        "https://api.openai.com/auth": {
            "chatgpt_account_id": account_id,
            "chatgpt_plan_type": "plus",
        },
        "https://api.openai.com/profile": {"email": email},
    }
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.sig"


def test_parse_manual_callback_url():
    code, state = codex_oauth.parse_manual_callback(
        "http://localhost:1455/auth/callback?code=abc&state=xyz"
    )
    assert code == "abc"
    assert state == "xyz"


def test_parse_manual_callback_bare_code():
    code, state = codex_oauth.parse_manual_callback("just-a-code")
    assert code == "just-a-code"
    assert state is None


def test_save_and_read_chatgpt_tokens(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    token = _fake_jwt()
    saved = codex_oauth.save_chatgpt_tokens(
        access_token=token,
        refresh_token="refresh-1",
        expires_in=3600,
        id_token="id-1",
    )
    assert saved["status"] == "success"
    assert (tmp_path / "auth.json").is_file()

    tokens = codex_oauth.read_stored_chatgpt_tokens()
    assert tokens is not None
    assert tokens["access_token"] == token
    assert tokens["account_id"] == "acct_123"
    assert tokens["email"] == "user@example.com"


def test_save_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    saved = codex_oauth.save_api_key("sk-test-key-123456")
    assert saved["auth_mode"] == "api"
    data = json.loads((tmp_path / "auth.json").read_text(encoding="utf-8"))
    assert data["OPENAI_API_KEY"] == "sk-test-key-123456"


def test_start_oauth_login_returns_url(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    # Avoid binding 1455 in CI if something else owns it — still returns URL.
    result = codex_oauth.start_oauth_login()
    assert result["status"] == "success"
    assert "authorize_url" in result
    assert "state" in result
    assert "auth.openai.com/oauth/authorize" in result["authorize_url"]
