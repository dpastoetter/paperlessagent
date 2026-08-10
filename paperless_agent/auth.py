"""Resolve OpenAI / Codex credentials for PaperlessAgent."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

AuthMode = Literal["api_key", "chatgpt_oauth", "none"]


def codex_home() -> Path:
    """Return CODEX_HOME (default ~/.codex)."""
    return Path(os.getenv("CODEX_HOME", Path.home() / ".codex")).expanduser()


def _read_codex_auth_json() -> dict[str, Any] | None:
    auth_path = codex_home() / "auth.json"
    if not auth_path.is_file():
        return None
    try:
        data = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def openai_api_key_from_codex() -> str | None:
    """Read an OpenAI Platform API key cached by Codex / this app."""
    data = _read_codex_auth_json()
    if not data:
        return None

    for key in ("OPENAI_API_KEY", "openai_api_key", "api_key"):
        value = data.get(key)
        if isinstance(value, str) and value.strip().startswith("sk-"):
            return value.strip()

    tokens = data.get("tokens")
    if isinstance(tokens, dict):
        for key in ("api_key", "OPENAI_API_KEY"):
            value = tokens.get(key)
            if isinstance(value, str) and value.strip().startswith("sk-"):
                return value.strip()

    return None


def resolve_openai_api_key() -> str | None:
    """
    Resolve OpenAI Platform API key from env, then Codex auth cache.

    Priority:
    1. OPENAI_API_KEY
    2. CODEX_API_KEY (explicit alias)
    3. ~/.codex/auth.json API-key login
    """
    for env_name in ("OPENAI_API_KEY", "CODEX_API_KEY"):
        value = os.getenv(env_name, "").strip()
        if value:
            return value

    return openai_api_key_from_codex()


def resolve_auth_mode() -> AuthMode:
    """Return the best available OpenAI auth mode."""
    if resolve_openai_api_key():
        return "api_key"
    # Lazy import avoids circular dependency at module import time
    from paperless_agent.codex_oauth import read_stored_chatgpt_tokens

    if read_stored_chatgpt_tokens():
        return "chatgpt_oauth"
    return "none"


def ensure_openai_env() -> str:
    """
    Ensure OPENAI_API_KEY is present for Platform API clients.

    ChatGPT OAuth is a separate path (Codex Responses backend) and does not
    populate OPENAI_API_KEY.
    """
    key = resolve_openai_api_key()
    if not key:
        raise RuntimeError(
            "No OpenAI API key found. Sign in with ChatGPT (OAuth) in the UI, "
            "or set OPENAI_API_KEY / save an API key in the Auth panel."
        )
    os.environ["OPENAI_API_KEY"] = key
    return key


def codex_auth_status() -> dict[str, Any]:
    """Return a non-secret summary of Codex/OpenAI auth availability."""
    from paperless_agent.codex_oauth import read_stored_chatgpt_tokens

    env_key = bool(os.getenv("OPENAI_API_KEY") or os.getenv("CODEX_API_KEY"))
    auth = _read_codex_auth_json()
    has_codex_file = auth is not None
    has_codex_api_key = openai_api_key_from_codex() is not None
    chatgpt = read_stored_chatgpt_tokens()
    mode = resolve_auth_mode()
    return {
        "auth_mode": mode,
        "openai_ready": mode != "none",
        "openai_key_from_env": env_key,
        "codex_auth_file_present": has_codex_file,
        "codex_api_key_present": has_codex_api_key,
        "codex_chatgpt_tokens_present": chatgpt is not None,
        "chatgpt_email": (chatgpt or {}).get("email"),
        "chatgpt_plan": (chatgpt or {}).get("plan_type"),
        "codex_home": str(codex_home()),
    }
