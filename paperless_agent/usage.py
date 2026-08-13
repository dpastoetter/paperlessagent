"""Process-lifetime LLM usage counters for the status chip."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Literal

Kind = Literal["chat", "embed"]

_lock = threading.Lock()
_state: dict[str, Any] = {
    "requests": 0,
    "chat_requests": 0,
    "embed_requests": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "last_provider": None,
    "last_model": None,
    "last_kind": None,
    "updated_at": None,
}


def reset_usage() -> None:
    """Clear counters (tests only)."""
    with _lock:
        _state.update(
            {
                "requests": 0,
                "chat_requests": 0,
                "embed_requests": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "last_provider": None,
                "last_model": None,
                "last_kind": None,
                "updated_at": None,
            }
        )


def _as_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def normalize_openai_usage(usage: Any) -> tuple[int, int, int]:
    """Extract prompt/completion/total from an OpenAI-style usage object."""
    if usage is None:
        return 0, 0, 0
    if isinstance(usage, dict):
        prompt = _as_int(usage.get("prompt_tokens") or usage.get("input_tokens"))
        completion = _as_int(usage.get("completion_tokens") or usage.get("output_tokens"))
        total = _as_int(usage.get("total_tokens"))
    else:
        prompt = _as_int(
            getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", None)
        )
        completion = _as_int(
            getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", None)
        )
        total = _as_int(getattr(usage, "total_tokens", None))
    if total <= 0:
        total = prompt + completion
    return prompt, completion, total


def normalize_gemini_usage(metadata: Any) -> tuple[int, int, int]:
    """Extract token counts from Gemini usage_metadata."""
    if metadata is None:
        return 0, 0, 0
    if isinstance(metadata, dict):
        prompt = _as_int(metadata.get("prompt_token_count"))
        completion = _as_int(metadata.get("candidates_token_count"))
        total = _as_int(metadata.get("total_token_count"))
    else:
        prompt = _as_int(getattr(metadata, "prompt_token_count", None))
        completion = _as_int(getattr(metadata, "candidates_token_count", None))
        total = _as_int(getattr(metadata, "total_token_count", None))
    if total <= 0:
        total = prompt + completion
    return prompt, completion, total


def normalize_ollama_usage(payload: dict[str, Any] | None) -> tuple[int, int, int]:
    """Treat Ollama eval counts as prompt/completion tokens."""
    if not payload:
        return 0, 0, 0
    prompt = _as_int(payload.get("prompt_eval_count"))
    completion = _as_int(payload.get("eval_count"))
    return prompt, completion, prompt + completion


def record_usage(
    provider: str,
    model: str | None = None,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int | None = None,
    kind: Kind = "chat",
) -> dict[str, Any]:
    """Accumulate one LLM/embed call into process-lifetime counters."""
    prompt = _as_int(prompt_tokens)
    completion = _as_int(completion_tokens)
    total = _as_int(total_tokens) if total_tokens is not None else prompt + completion
    if total <= 0:
        total = prompt + completion

    with _lock:
        _state["requests"] = int(_state["requests"]) + 1
        if kind == "embed":
            _state["embed_requests"] = int(_state["embed_requests"]) + 1
        else:
            _state["chat_requests"] = int(_state["chat_requests"]) + 1
        _state["prompt_tokens"] = int(_state["prompt_tokens"]) + prompt
        _state["completion_tokens"] = int(_state["completion_tokens"]) + completion
        _state["total_tokens"] = int(_state["total_tokens"]) + total
        _state["last_provider"] = (provider or "").strip() or None
        _state["last_model"] = (model or "").strip() or None
        _state["last_kind"] = kind
        _state["updated_at"] = datetime.now(timezone.utc).isoformat()
        return usage_snapshot_unlocked()


def usage_snapshot_unlocked() -> dict[str, Any]:
    return {
        "requests": int(_state["requests"]),
        "chat_requests": int(_state["chat_requests"]),
        "embed_requests": int(_state["embed_requests"]),
        "prompt_tokens": int(_state["prompt_tokens"]),
        "completion_tokens": int(_state["completion_tokens"]),
        "total_tokens": int(_state["total_tokens"]),
        "last_provider": _state["last_provider"],
        "last_model": _state["last_model"],
        "last_kind": _state["last_kind"],
        "updated_at": _state["updated_at"],
    }


def usage_snapshot() -> dict[str, Any]:
    """Return a copy of current process usage for /api/health."""
    with _lock:
        return usage_snapshot_unlocked()
