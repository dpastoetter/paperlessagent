"""Ollama detection, model checks, .env persistence, and runtime provider switch."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

from paperless_agent import config

DEFAULT_CHAT_MODEL = "gemma3"
DEFAULT_EMBED_MODEL = "nomic-embed-text"
PROBE_TIMEOUT = 0.6
PULL_TIMEOUT = 600.0
_TAGS_CACHE_TTL = 30.0

_ENV_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
# (expires_at, base_url, models)
_tags_cache: tuple[float, str, list[str]] | None = None


def env_path() -> Path:
    return config.PROJECT_ROOT / ".env"


def model_name_matches(installed: str, wanted: str) -> bool:
    """True if an Ollama tag satisfies the configured model name."""
    inst = (installed or "").strip().lower()
    want = (wanted or "").strip().lower()
    if not inst or not want:
        return False
    if inst == want:
        return True
    if inst.startswith(f"{want}:"):
        return True
    return inst.split(":", 1)[0] == want.split(":", 1)[0]


def tags_include(models: list[str], wanted: str) -> bool:
    return any(model_name_matches(name, wanted) for name in models)


def resolve_installed_model(wanted: str, installed: list[str]) -> str | None:
    """
    Map a configured name (e.g. gemma3) to an installed Ollama tag (e.g. gemma3:4b).

    Ollama does not always alias bare names to size-tagged variants, so API calls
    must use a concrete local tag.
    """
    want = (wanted or "").strip()
    if not want:
        return None
    matches = [name for name in installed if model_name_matches(name, want)]
    if not matches:
        return None

    want_l = want.lower()

    def rank(name: str) -> tuple[int, int, str]:
        lower = name.lower()
        if lower == want_l:
            return (0, 0, lower)
        if lower == f"{want_l}:latest":
            return (1, 0, lower)
        # Prefer shorter concrete tags (gemma3:4b before gemma3:27b).
        return (2, len(lower), lower)

    return sorted(matches, key=rank)[0]


def clear_ollama_tags_cache() -> None:
    global _tags_cache
    _tags_cache = None


def list_installed_models(base_url: str | None = None) -> list[str]:
    """Cached list of local Ollama model tags."""
    global _tags_cache
    url = (base_url or config.OLLAMA_BASE_URL).rstrip("/")
    now = time.monotonic()
    if _tags_cache is not None:
        expires_at, cached_url, models = _tags_cache
        if cached_url == url and now < expires_at:
            return list(models)
    probe = probe_ollama(url)
    models = list(probe.get("models") or []) if probe.get("reachable") else []
    _tags_cache = (now + _TAGS_CACHE_TTL, url, models)
    return list(models)


def resolve_runtime_model(wanted: str, *, base_url: str | None = None) -> str:
    """Resolve configured model to an installed tag; fall back to the configured name."""
    installed = list_installed_models(base_url)
    return resolve_installed_model(wanted, installed) or (wanted or "").strip()


def probe_ollama(base_url: str | None = None, *, timeout: float = PROBE_TIMEOUT) -> dict[str, Any]:
    """
    Probe a local Ollama server.

    Returns a status dict; never raises for connectivity failures.
    """
    url = (base_url or config.OLLAMA_BASE_URL).rstrip("/")
    result: dict[str, Any] = {
        "reachable": False,
        "base_url": url,
        "models": [],
        "version": None,
        "error": None,
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            tags_resp = client.get(f"{url}/api/tags")
            tags_resp.raise_for_status()
            payload = tags_resp.json()
            models = [
                str(item.get("name") or "")
                for item in (payload.get("models") or [])
                if isinstance(item, dict) and item.get("name")
            ]
            result["models"] = models
            result["reachable"] = True
            try:
                ver_resp = client.get(f"{url}/api/version")
                if ver_resp.is_success:
                    result["version"] = (ver_resp.json() or {}).get("version")
            except (httpx.HTTPError, ValueError):
                pass
    except httpx.ConnectError:
        result["error"] = f"Cannot reach Ollama at {url} — is `ollama serve` running?"
    except httpx.HTTPError as exc:
        result["error"] = f"Ollama at {url} returned an error: {exc}"
    except ValueError as exc:
        result["error"] = f"Unexpected Ollama response: {exc}"
    return result


def ollama_reachable(base_url: str | None = None, *, timeout: float = PROBE_TIMEOUT) -> bool:
    return bool(probe_ollama(base_url, timeout=timeout).get("reachable"))


def required_models(
    *,
    chat_model: str | None = None,
    embed_model: str | None = None,
) -> tuple[str, str]:
    chat = (chat_model or config.MODEL_NAME or DEFAULT_CHAT_MODEL).strip() or DEFAULT_CHAT_MODEL
    embed = (
        embed_model or config.EMBEDDING_MODEL or DEFAULT_EMBED_MODEL
    ).strip() or DEFAULT_EMBED_MODEL
    # If leftover cloud model names are still in config, fall back to Ollama defaults.
    if chat.startswith(("gpt-", "gemini", "text-embedding")):
        chat = DEFAULT_CHAT_MODEL
    if embed.startswith(("text-embedding", "gemini")):
        embed = DEFAULT_EMBED_MODEL
    return chat, embed


def missing_models(installed: list[str], *, chat: str, embed: str) -> list[str]:
    missing: list[str] = []
    if not tags_include(installed, chat):
        missing.append(chat)
    if embed != chat and not tags_include(installed, embed):
        missing.append(embed)
    return missing


def pull_hint(models: list[str]) -> str:
    if not models:
        return ""
    return " && ".join(f"ollama pull {name}" for name in models)


def format_http_error(exc: Exception, *, model: str, kind: str = "model") -> str:
    """Turn Ollama HTTP failures into actionable pull / serve hints."""
    text = str(exc)
    lower = text.lower()
    if "not found" in lower or "404" in lower or "pull" in lower:
        return (
            f"Ollama {kind} '{model}' is not available locally. "
            f"Run: ollama pull {model}"
        )
    return f"Ollama request failed for {kind} '{model}': {text}"


def upsert_env_values(updates: dict[str, str], path: Path | None = None) -> Path:
    """Create or update keys in `.env` without dropping unrelated lines/comments."""
    target = path or env_path()
    lines: list[str] = []
    if target.exists():
        lines = target.read_text(encoding="utf-8").splitlines()

    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        match = _ENV_KEY_RE.match(line.strip()) if line.strip() and not line.lstrip().startswith("#") else None
        if not match:
            out.append(line)
            continue
        key = match.group(1)
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)

    if remaining:
        if out and out[-1].strip():
            out.append("")
        for key, value in remaining.items():
            out.append(f"{key}={value}")

    target.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(out)
    if text and not text.endswith("\n"):
        text += "\n"
    tmp = target.with_suffix(".env.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(target)
    return target


def apply_llm_provider(
    provider: str,
    *,
    model: str | None = None,
    embedding_model: str | None = None,
    base_url: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """
    Switch the active LLM provider at runtime and optionally persist to `.env`.

    Updates `paperless_agent.config` globals used throughout the process.
    """
    normalized = (provider or "").strip().lower()
    aliases = {
        "google": "gemini",
        "codex": "openai",
        "local": "ollama",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"openai", "gemini", "ollama"}:
        raise ValueError("provider must be one of: openai, gemini, ollama")

    if normalized == "ollama":
        chat, embed = required_models(chat_model=model, embed_model=embedding_model)
        url = (base_url or config.OLLAMA_BASE_URL or "http://localhost:11434").rstrip("/")
    elif normalized == "openai":
        chat = (model or "gpt-5.6-luna").strip() or "gpt-5.6-luna"
        embed = (embedding_model or "text-embedding-3-small").strip() or "text-embedding-3-small"
        url = config.OLLAMA_BASE_URL
    else:
        chat = (model or "gemini-flash-latest").strip() or "gemini-flash-latest"
        embed = (embedding_model or "text-embedding-004").strip() or "text-embedding-004"
        url = config.OLLAMA_BASE_URL

    config.LLM_PROVIDER = normalized
    config.MODEL_NAME = chat
    config.EMBEDDING_MODEL = embed
    if normalized == "ollama":
        config.OLLAMA_BASE_URL = url

    os.environ["PAPERLESS_LLM_PROVIDER"] = normalized
    os.environ["PAPERLESS_MODEL"] = chat
    os.environ["PAPERLESS_EMBEDDING_MODEL"] = embed
    if normalized == "ollama":
        os.environ["OLLAMA_BASE_URL"] = url
        # Point OpenAI-compatible clients at Ollama for ADK OpenAILlm usage.
        os.environ["OPENAI_BASE_URL"] = f"{url}/v1"
        if not os.environ.get("OPENAI_API_KEY"):
            os.environ["OPENAI_API_KEY"] = "ollama"
    else:
        # Clear leftovers from Ollama's OpenAI-compatible endpoint.
        base = os.environ.get("OPENAI_BASE_URL", "")
        if "11434" in base or "ollama" in base.lower():
            os.environ.pop("OPENAI_BASE_URL", None)
        if os.environ.get("OPENAI_API_KEY") == "ollama":
            os.environ.pop("OPENAI_API_KEY", None)

    if persist:
        updates = {
            "PAPERLESS_LLM_PROVIDER": normalized,
            "PAPERLESS_MODEL": chat,
            "PAPERLESS_EMBEDDING_MODEL": embed,
        }
        if normalized == "ollama":
            updates["OLLAMA_BASE_URL"] = url
        upsert_env_values(updates)

    return {
        "provider": normalized,
        "model": chat,
        "embedding_model": embed,
        "ollama_base_url": config.OLLAMA_BASE_URL,
        "persisted": persist,
    }


def ollama_status(*, base_url: str | None = None) -> dict[str, Any]:
    """Full status payload for Settings UI / health enrichment."""
    url = (base_url or config.OLLAMA_BASE_URL).rstrip("/")
    chat, embed = required_models()
    probe = probe_ollama(url)
    installed = list(probe.get("models") or [])
    # Keep the tags cache warm for subsequent chat/embed calls.
    global _tags_cache
    if probe.get("reachable"):
        _tags_cache = (time.monotonic() + _TAGS_CACHE_TTL, url, installed)
    missing = missing_models(installed, chat=chat, embed=embed) if probe["reachable"] else [chat, embed]
    resolved_chat = resolve_installed_model(chat, installed)
    resolved_embed = resolve_installed_model(embed, installed)
    active = config.LLM_PROVIDER == "ollama"
    ready = bool(probe["reachable"] and not missing and active)
    return {
        "active": active,
        "ready": ready,
        "reachable": probe["reachable"],
        "base_url": url,
        "version": probe.get("version"),
        "installed_models": installed,
        "chat_model": chat,
        "embedding_model": embed,
        "resolved_chat_model": resolved_chat,
        "resolved_embedding_model": resolved_embed,
        "missing_models": missing,
        "pull_command": pull_hint(missing),
        "error": probe.get("error"),
        "install_hint": (
            "Install Ollama from https://ollama.com/download then run `ollama serve`."
            if not probe["reachable"]
            else None
        ),
    }


def enable_ollama(
    *,
    base_url: str | None = None,
    chat_model: str | None = None,
    embedding_model: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Switch to Ollama with local defaults and return fresh status."""
    applied = apply_llm_provider(
        "ollama",
        model=chat_model or DEFAULT_CHAT_MODEL,
        embedding_model=embedding_model or DEFAULT_EMBED_MODEL,
        base_url=base_url,
        persist=persist,
    )
    status = ollama_status(base_url=applied["ollama_base_url"])
    return {"status": "success", "applied": applied, "ollama": status}


def pull_model(model: str, *, base_url: str | None = None) -> dict[str, Any]:
    """Pull a model via the Ollama HTTP API (blocking until finished)."""
    name = (model or "").strip()
    if not name:
        raise ValueError("model name is required")
    url = (base_url or config.OLLAMA_BASE_URL).rstrip("/")
    try:
        with httpx.Client(timeout=PULL_TIMEOUT) as client:
            resp = client.post(
                f"{url}/api/pull",
                json={"name": name, "stream": False},
            )
            resp.raise_for_status()
            payload = resp.json() if resp.content else {}
    except httpx.ConnectError as exc:
        raise RuntimeError(
            f"Cannot reach Ollama at {url} — is `ollama serve` running?"
        ) from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(format_http_error(exc, model=name, kind="pull")) from exc

    clear_ollama_tags_cache()
    status = ollama_status(base_url=url)
    return {
        "status": "success",
        "model": name,
        "pull": payload if isinstance(payload, dict) else {},
        "ollama": status,
    }
