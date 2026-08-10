"""Tests for OpenAI / Codex credential resolution."""

from __future__ import annotations

import json

import pytest

from paperless_agent import auth


def test_resolve_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-from-env")
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert auth.resolve_openai_api_key() == "sk-test-from-env"


def test_resolve_from_codex_auth_json(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text(
        json.dumps({"OPENAI_API_KEY": "sk-from-codex"}),
        encoding="utf-8",
    )
    assert auth.resolve_openai_api_key() == "sk-from-codex"


def test_chatgpt_tokens_enable_oauth_mode(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    # Minimal JWT with chatgpt_account_id claim
    import base64

    payload = base64.urlsafe_b64encode(
        json.dumps(
            {
                "https://api.openai.com/auth": {"chatgpt_account_id": "acct_1"},
                "https://api.openai.com/profile": {"email": "a@b.c"},
            }
        ).encode()
    ).rstrip(b"=").decode()
    access = f"eyJhbGciOiJub25lIn0.{payload}.sig"
    (tmp_path / "auth.json").write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": access,
                    "refresh_token": "chatgpt-refresh",
                    "account_id": "acct_1",
                },
                "email": "a@b.c",
            }
        ),
        encoding="utf-8",
    )
    assert auth.resolve_openai_api_key() is None
    assert auth.resolve_auth_mode() == "chatgpt_oauth"
    status = auth.codex_auth_status()
    assert status["codex_chatgpt_tokens_present"] is True
    assert status["openai_ready"] is True


def test_ensure_openai_env_sets_process_env(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text(
        json.dumps({"OPENAI_API_KEY": "sk-seeded"}),
        encoding="utf-8",
    )
    import os

    key = auth.ensure_openai_env()
    assert key == "sk-seeded"
    assert os.environ["OPENAI_API_KEY"] == "sk-seeded"


def test_ensure_openai_env_raises_without_key(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    with pytest.raises(RuntimeError, match="No OpenAI API key"):
        auth.ensure_openai_env()
