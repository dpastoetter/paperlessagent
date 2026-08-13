"""LLM provider interface (text, vision, embeddings, health, usage)."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LlmProvider(Protocol):
    """Small surface shared by OpenAI, Codex OAuth, Gemini, and Ollama backends."""

    @property
    def name(self) -> str:
        """Configured provider id (openai | gemini | ollama)."""

    async def complete_text(
        self,
        prompt: str,
        *,
        instructions: str,
        cancel_event: asyncio.Event | None = None,
        json_mode: bool = False,
    ) -> str:
        """Plain-text completion."""

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
        """Multimodal completion with page images (vision OCR)."""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embedding vectors for RAG (backend may be local ONNX, Ollama, or cloud)."""

    def health(self) -> dict[str, Any]:
        """Provider readiness / model identity for status UIs."""

    def usage(self) -> dict[str, Any]:
        """Process-lifetime token/request counters."""
