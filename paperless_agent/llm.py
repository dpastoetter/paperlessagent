"""Provider-aware LLM model factory for ADK agents."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.labs.openai import OpenAILlm, OpenAIResponsesLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from openai import AsyncOpenAI

from paperless_agent.auth import resolve_auth_mode, resolve_openai_api_key
from paperless_agent.codex_oauth import (
    CODEX_RESPONSES_BASE_URL,
    ORIGINATOR,
    get_valid_chatgpt_tokens,
)
from paperless_agent.config import LLM_PROVIDER, MODEL_NAME

# ChatGPT Codex backend rejects many Platform API model IDs (e.g. gpt-4.1).
CODEX_DEFAULT_MODEL = os.getenv("PAPERLESS_CODEX_MODEL", "gpt-5.6-luna").strip() or "gpt-5.6-luna"
_CODEX_MODEL_PREFIXES = ("gpt-5.6", "gpt-5.5", "gpt-5.4", "gpt-5.3-codex", "codex-")


class CodexResponsesLlm(OpenAIResponsesLlm):
    """OpenAIResponsesLlm constrained for ChatGPT Codex subscription OAuth."""

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        # Codex OAuth is SSE-only; ignore caller stream=False.
        async for response in super().generate_content_async(llm_request, stream=True):
            yield response

    def _get_response_create_kwargs(
        self, llm_request: LlmRequest, *, stream: bool
    ) -> dict[str, Any]:
        kwargs = super()._get_response_create_kwargs(llm_request, stream=True)
        kwargs["store"] = False
        kwargs["stream"] = True
        if not kwargs.get("instructions"):
            kwargs["instructions"] = (
                "You are PaperlessAgent, a document classification and "
                "extraction assistant."
            )
        for key in ("temperature", "top_p", "max_output_tokens", "service_tier"):
            kwargs.pop(key, None)
        return kwargs


def resolve_model_name() -> str:
    """Pick a model ID valid for the active auth mode."""
    if LLM_PROVIDER != "openai":
        return MODEL_NAME

    mode = resolve_auth_mode()
    if mode != "chatgpt_oauth":
        return MODEL_NAME

    configured = os.getenv("PAPERLESS_CODEX_MODEL", "").strip() or MODEL_NAME
    if configured.startswith(_CODEX_MODEL_PREFIXES) or configured in {
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.6",
    }:
        return configured
    return CODEX_DEFAULT_MODEL


def _build_codex_responses_llm(model_name: str) -> CodexResponsesLlm:
    """Build a Codex Responses LLM with a ChatGPT OAuth-backed client."""
    tokens = get_valid_chatgpt_tokens()
    if not tokens:
        raise RuntimeError("ChatGPT OAuth tokens are missing; sign in again")

    client = AsyncOpenAI(
        api_key=tokens["access_token"],
        base_url=CODEX_RESPONSES_BASE_URL,
        default_headers={
            "ChatGPT-Account-Id": tokens["account_id"],
            "originator": ORIGINATOR,
        },
    )
    return CodexResponsesLlm(model=model_name, client=client, store=False)


def get_model() -> Any:
    """
    Return the ADK model object/string for the configured provider.

    - gemini: model name string (uses GOOGLE_API_KEY)
    - openai + API key: OpenAILlm (api.openai.com)
    - openai + ChatGPT OAuth: CodexResponsesLlm via Codex backend
    """
    model_name = resolve_model_name()
    if LLM_PROVIDER != "openai":
        return model_name

    mode = resolve_auth_mode()
    if mode == "chatgpt_oauth":
        return _build_codex_responses_llm(model_name)

    key = resolve_openai_api_key()
    if key:
        os.environ["OPENAI_API_KEY"] = key
    return OpenAILlm(model=model_name)


async def complete_text(prompt: str, *, instructions: str) -> str:
    """
    Non-ADK text completion for the active provider.

    Prefer this over ADK tool agents when using ChatGPT OAuth / Codex streaming,
    which often yields empty final text parts through the ADK Runner.
    """
    model_name = resolve_model_name()
    if LLM_PROVIDER == "openai":
        mode = resolve_auth_mode()
        if mode == "chatgpt_oauth":
            return await _complete_codex(prompt, instructions=instructions, model_name=model_name)
        return await _complete_openai_api(
            prompt, instructions=instructions, model_name=model_name
        )
    return await _complete_gemini(prompt, instructions=instructions, model_name=model_name)


async def _complete_codex(prompt: str, *, instructions: str, model_name: str) -> str:
    tokens = get_valid_chatgpt_tokens()
    if not tokens:
        raise RuntimeError("ChatGPT OAuth tokens are missing; sign in again")

    client = AsyncOpenAI(
        api_key=tokens["access_token"],
        base_url=CODEX_RESPONSES_BASE_URL,
        default_headers={
            "ChatGPT-Account-Id": tokens["account_id"],
            "originator": ORIGINATOR,
        },
    )
    stream = await client.responses.create(
        model=model_name,
        instructions=instructions,
        input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        store=False,
        stream=True,
    )
    chunks: list[str] = []
    async for event in stream:
        etype = getattr(event, "type", None)
        if etype == "response.output_text.delta":
            delta = getattr(event, "delta", None)
            if delta:
                chunks.append(delta)
        elif etype == "response.completed":
            response = getattr(event, "response", None)
            output_text = getattr(response, "output_text", None) if response else None
            if output_text:
                return str(output_text).strip()
    return "".join(chunks).strip()


async def _complete_openai_api(prompt: str, *, instructions: str, model_name: str) -> str:
    from paperless_agent.auth import ensure_openai_env

    ensure_openai_env()
    client = AsyncOpenAI()
    response = await client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": instructions},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    return (response.choices[0].message.content or "").strip()


async def _complete_gemini(prompt: str, *, instructions: str, model_name: str) -> str:
    from google import genai

    from paperless_agent.config import GOOGLE_API_KEY

    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY is not set")
    client = genai.Client(api_key=GOOGLE_API_KEY)
    response = client.models.generate_content(
        model=model_name,
        contents=f"{instructions}\n\n{prompt}",
    )
    return (getattr(response, "text", None) or "").strip()


async def complete_with_images(
    prompt: str,
    *,
    images: list[bytes],
    instructions: str,
    mime_type: str = "image/png",
) -> str:
    """
    Multimodal completion with one or more page images (PNG/JPEG bytes).

    Used for AI vision OCR of scanned page images.
    """
    if not images:
        raise ValueError("images must not be empty")

    model_name = resolve_model_name()
    if LLM_PROVIDER == "openai":
        mode = resolve_auth_mode()
        if mode == "chatgpt_oauth":
            return await _complete_codex_images(
                prompt,
                images=images,
                instructions=instructions,
                model_name=model_name,
                mime_type=mime_type,
            )
        return await _complete_openai_images(
            prompt,
            images=images,
            instructions=instructions,
            model_name=model_name,
            mime_type=mime_type,
        )
    return await _complete_gemini_images(
        prompt,
        images=images,
        instructions=instructions,
        model_name=model_name,
        mime_type=mime_type,
    )


async def _complete_codex_images(
    prompt: str,
    *,
    images: list[bytes],
    instructions: str,
    model_name: str,
    mime_type: str,
) -> str:
    from paperless_agent.ocr import images_to_data_urls

    tokens = get_valid_chatgpt_tokens()
    if not tokens:
        raise RuntimeError("ChatGPT OAuth tokens are missing; sign in again")

    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    for url in images_to_data_urls(images, mime_type=mime_type):
        content.append({"type": "input_image", "image_url": url})

    client = AsyncOpenAI(
        api_key=tokens["access_token"],
        base_url=CODEX_RESPONSES_BASE_URL,
        default_headers={
            "ChatGPT-Account-Id": tokens["account_id"],
            "originator": ORIGINATOR,
        },
    )
    stream = await client.responses.create(
        model=model_name,
        instructions=instructions,
        input=[{"role": "user", "content": content}],
        store=False,
        stream=True,
    )
    chunks: list[str] = []
    async for event in stream:
        etype = getattr(event, "type", None)
        if etype == "response.output_text.delta":
            delta = getattr(event, "delta", None)
            if delta:
                chunks.append(delta)
        elif etype == "response.completed":
            response = getattr(event, "response", None)
            output_text = getattr(response, "output_text", None) if response else None
            if output_text:
                return str(output_text).strip()
    return "".join(chunks).strip()


async def _complete_openai_images(
    prompt: str,
    *,
    images: list[bytes],
    instructions: str,
    model_name: str,
    mime_type: str,
) -> str:
    from paperless_agent.auth import ensure_openai_env
    from paperless_agent.ocr import images_to_data_urls

    ensure_openai_env()
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for url in images_to_data_urls(images, mime_type=mime_type):
        content.append({"type": "image_url", "image_url": {"url": url}})

    client = AsyncOpenAI()
    response = await client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": instructions},
            {"role": "user", "content": content},
        ],
        temperature=0.1,
    )
    return (response.choices[0].message.content or "").strip()


async def _complete_gemini_images(
    prompt: str,
    *,
    images: list[bytes],
    instructions: str,
    model_name: str,
    mime_type: str,
) -> str:
    from google import genai
    from google.genai import types

    from paperless_agent.config import GOOGLE_API_KEY

    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY is not set")
    client = genai.Client(api_key=GOOGLE_API_KEY)
    parts: list[Any] = [types.Part.from_text(text=f"{instructions}\n\n{prompt}")]
    for raw in images:
        parts.append(types.Part.from_bytes(data=raw, mime_type=mime_type))
    response = client.models.generate_content(
        model=model_name,
        contents=parts,
    )
    return (getattr(response, "text", None) or "").strip()
