"""Active LLM provider that dispatches to backends in paperless_agent.llm."""

from __future__ import annotations

import asyncio
from typing import Any

from paperless_agent import config
from paperless_agent.providers.base import LlmProvider
from paperless_agent.usage import usage_snapshot


class ActiveLlmProvider:
    """
    Default provider implementation.

    Keeps call sites on a stable interface while backend SDK details remain in
    ``paperless_agent.llm`` (ADK model factory + per-backend completions).
    """

    @property
    def name(self) -> str:
        return (config.LLM_PROVIDER or "openai").strip().lower() or "openai"

    async def complete_text(
        self,
        prompt: str,
        *,
        instructions: str,
        cancel_event: asyncio.Event | None = None,
        json_mode: bool = False,
    ) -> str:
        # Late import: paperless_agent.llm imports get_llm_provider for its public API.
        from paperless_agent import llm as llm_mod

        return await llm_mod.complete_text_via_backend(
            prompt,
            instructions=instructions,
            cancel_event=cancel_event,
            json_mode=json_mode,
        )

    async def complete_vision(
        self,
        prompt: str,
        *,
        images: list[bytes],
        instructions: str,
        mime_type: str = "image/png",
        cancel_event: asyncio.Event | None = None,
        timeout: float | None = None,
        ollama_options: dict[str, Any] | None = None,
    ) -> str:
        # Late import: avoids llm ↔ providers circular dependency at module load.
        from paperless_agent import llm as llm_mod

        return await llm_mod.complete_with_images_via_backend(
            prompt,
            images=images,
            instructions=instructions,
            mime_type=mime_type,
            cancel_event=cancel_event,
            timeout=timeout,
            ollama_options=ollama_options,
        )

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        # Late import: rag_index pulls embeddings config / optional SDKs.
        from paperless_agent.tools.rag_index import embed_texts

        return embed_texts(texts)

    def health(self) -> dict[str, Any]:
        # Late import: ollama_setup / llm resolve_model_name.
        from paperless_agent.llm import resolve_model_name
        from paperless_agent.ollama_setup import ollama_status

        payload: dict[str, Any] = {
            "provider": self.name,
            "model": resolve_model_name(),
            "configured_model": config.MODEL_NAME,
            "embedding_model": config.EMBEDDING_MODEL,
            "ready": True,
        }
        if self.name == "ollama":
            ollama = ollama_status()
            payload["ollama"] = ollama
            payload["ready"] = bool(ollama.get("ready"))
            if not payload["ready"]:
                payload["error"] = ollama.get("error") or "Local Ollama is not ready"
        return payload

    def usage(self) -> dict[str, Any]:
        return usage_snapshot()


def get_llm_provider() -> LlmProvider:
    """Return the process-wide active provider (thin wrapper today)."""
    return ActiveLlmProvider()
