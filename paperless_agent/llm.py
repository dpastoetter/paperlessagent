"""Provider-aware LLM model factory for ADK agents."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from google.adk.labs.openai import OpenAILlm, OpenAIResponsesLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from openai import AsyncOpenAI

from paperless_agent import config
from paperless_agent.auth import resolve_auth_mode, resolve_openai_api_key
from paperless_agent.job_control import FileCancelledError
from paperless_agent.codex_oauth import (
    CODEX_RESPONSES_BASE_URL,
    ORIGINATOR,
    get_valid_chatgpt_tokens,
)
from paperless_agent.ollama_setup import (
    ensure_ollama_ready,
    format_http_error,
    resolve_runtime_model,
)
from paperless_agent.usage import (
    normalize_gemini_usage,
    normalize_ollama_usage,
    normalize_openai_usage,
    record_usage,
)

# ChatGPT Codex backend rejects many Platform API model IDs (e.g. gpt-4.1).
CODEX_DEFAULT_MODEL = os.getenv("PAPERLESS_CODEX_MODEL", "gpt-5.6-luna").strip() or "gpt-5.6-luna"
_CODEX_MODEL_PREFIXES = ("gpt-5.6", "gpt-5.5", "gpt-5.4", "gpt-5.3-codex", "codex-")

# Bound every provider so a hung LLM call cannot stall the inbox forever.
# Vision OCR gets a longer budget; text completion stays shorter.
LLM_TEXT_TIMEOUT = float(os.getenv("PAPERLESS_LLM_TIMEOUT", "120"))
LLM_VISION_TIMEOUT = float(os.getenv("PAPERLESS_LLM_VISION_TIMEOUT", "300"))
OLLAMA_CHAT_TIMEOUT = float(os.getenv("PAPERLESS_OLLAMA_TIMEOUT", "300"))


OLLAMA_CHAT_TIMEOUT = float(os.getenv("PAPERLESS_OLLAMA_TIMEOUT", "300"))
# httpx/Ollama may keep a blocking read open after asyncio cancel; don't hang the UI.
CANCEL_JOIN_TIMEOUT = float(os.getenv("PAPERLESS_CANCEL_JOIN_TIMEOUT", "3"))


async def _join_cancelled(task: asyncio.Task[Any]) -> None:
    """Best-effort wait for a cancelled task; never block the event loop for long."""
    if task.done():
        with contextlib.suppress(asyncio.CancelledError, Exception):
            task.result()
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError, Exception):
        await asyncio.wait_for(task, timeout=CANCEL_JOIN_TIMEOUT)


async def run_cancellable(
    coro,
    *,
    cancel_event: asyncio.Event | None = None,
    timeout: float | None = None,
) -> Any:
    """Run a coroutine, aborting when cancel_event is set or timeout elapses."""
    if cancel_event is None:
        if timeout is not None:
            return await asyncio.wait_for(coro, timeout=timeout)
        return await coro

    task = asyncio.create_task(coro)
    cancel_wait = asyncio.create_task(cancel_event.wait())
    try:
        wait_set: set[asyncio.Task[Any]] = {task, cancel_wait}
        done, _pending = await asyncio.wait(
            wait_set,
            return_when=asyncio.FIRST_COMPLETED,
            timeout=timeout,
        )
        if not done:
            await _join_cancelled(task)
            cancel_wait.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cancel_wait
            raise TimeoutError(f"LLM call timed out after {timeout:.0f}s")
        if cancel_wait in done:
            await _join_cancelled(task)
            raise FileCancelledError("File processing was cancelled")
        cancel_wait.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cancel_wait
        return task.result()
    except asyncio.CancelledError:
        await _join_cancelled(task)
        cancel_wait.cancel()
        raise


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
    if config.LLM_PROVIDER != "openai":
        return config.MODEL_NAME

    mode = resolve_auth_mode()
    if mode != "chatgpt_oauth":
        return config.MODEL_NAME

    configured = os.getenv("PAPERLESS_CODEX_MODEL", "").strip() or config.MODEL_NAME
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
        timeout=LLM_TEXT_TIMEOUT,
        max_retries=2,
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
    - ollama: OpenAILlm against Ollama's OpenAI-compatible endpoint
    """
    model_name = resolve_model_name()
    if config.LLM_PROVIDER == "ollama":
        # OpenAILlm builds its AsyncOpenAI() from env; point it at Ollama's
        # OpenAI-compatible endpoint (any non-empty api key is accepted).
        # Readiness is checked at request time in _complete_ollama / embeddings.
        os.environ["OPENAI_BASE_URL"] = f"{config.OLLAMA_BASE_URL}/v1"
        if not os.environ.get("OPENAI_API_KEY"):
            os.environ["OPENAI_API_KEY"] = "ollama"
        return OpenAILlm(model=resolve_runtime_model(model_name))
    if config.LLM_PROVIDER != "openai":
        return model_name

    mode = resolve_auth_mode()
    if mode == "chatgpt_oauth":
        return _build_codex_responses_llm(model_name)

    key = resolve_openai_api_key()
    if key:
        os.environ["OPENAI_API_KEY"] = key
    return OpenAILlm(model=model_name)


def _record_openai_response(provider: str, model_name: str, response: Any) -> None:
    prompt, completion, total = normalize_openai_usage(getattr(response, "usage", None))
    record_usage(
        provider,
        model_name,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        kind="chat",
    )


def _record_codex_completed(model_name: str, response: Any) -> None:
    usage = getattr(response, "usage", None) if response is not None else None
    prompt, completion, total = normalize_openai_usage(usage)
    record_usage(
        "openai",
        model_name,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        kind="chat",
    )


def _record_gemini_response(model_name: str, response: Any) -> None:
    metadata = getattr(response, "usage_metadata", None)
    prompt, completion, total = normalize_gemini_usage(metadata)
    record_usage(
        "gemini",
        model_name,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        kind="chat",
    )


async def complete_text(
    prompt: str,
    *,
    instructions: str,
    cancel_event: asyncio.Event | None = None,
    json_mode: bool = False,
) -> str:
    """
    Non-ADK text completion for the active provider.

    Prefer this over ADK tool agents when using ChatGPT OAuth / Codex streaming,
    which often yields empty final text parts through the ADK Runner.
    """
    model_name = resolve_model_name()
    if config.LLM_PROVIDER == "ollama":
        timeout = max(LLM_TEXT_TIMEOUT, OLLAMA_CHAT_TIMEOUT)
        coro = _complete_ollama(
            prompt,
            instructions=instructions,
            model_name=model_name,
            cancel_event=cancel_event,
            timeout=timeout,
        )
    elif config.LLM_PROVIDER == "openai":
        mode = resolve_auth_mode()
        if mode == "chatgpt_oauth":
            coro = _complete_codex(
                prompt,
                instructions=instructions,
                model_name=model_name,
            )
        else:
            coro = _complete_openai_api(
                prompt,
                instructions=instructions,
                model_name=model_name,
                json_mode=json_mode,
            )
        timeout = LLM_TEXT_TIMEOUT
    else:
        coro = _complete_gemini(prompt, instructions=instructions, model_name=model_name)
        timeout = LLM_TEXT_TIMEOUT
    try:
        return await run_cancellable(coro, cancel_event=cancel_event, timeout=timeout)
    except TimeoutError as exc:
        raise RuntimeError(
            f"LLM text completion timed out after {timeout:.0f}s"
        ) from exc


async def _collect_codex_stream(stream: Any, model_name: str) -> str:
    """Merge Codex SSE deltas and final output_text into one string."""
    chunks: list[str] = []
    recorded = False
    output_text = ""
    async for event in stream:
        etype = getattr(event, "type", None)
        if etype == "response.output_text.delta":
            delta = getattr(event, "delta", None)
            if delta:
                chunks.append(delta)
        elif etype == "response.completed":
            response = getattr(event, "response", None)
            _record_codex_completed(model_name, response)
            recorded = True
            final = getattr(response, "output_text", None) if response else None
            if final:
                output_text = str(final).strip()
    if not recorded:
        record_usage("openai", model_name, kind="chat")
    streamed = "".join(chunks).strip()
    if streamed and output_text:
        return streamed if len(streamed) >= len(output_text) else output_text
    return streamed or output_text


async def _complete_codex(
    prompt: str,
    *,
    instructions: str,
    model_name: str,
) -> str:
    tokens = get_valid_chatgpt_tokens()
    if not tokens:
        raise RuntimeError("ChatGPT OAuth tokens are missing; sign in again")

    client = AsyncOpenAI(
        api_key=tokens["access_token"],
        base_url=CODEX_RESPONSES_BASE_URL,
        timeout=LLM_TEXT_TIMEOUT,
        max_retries=2,
        default_headers={
            "ChatGPT-Account-Id": tokens["account_id"],
            "originator": ORIGINATOR,
        },
    )
    create_kwargs: dict[str, Any] = {
        "model": model_name,
        "instructions": instructions,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        "store": False,
        "stream": True,
    }
    stream = await client.responses.create(**create_kwargs)
    return await _collect_codex_stream(stream, model_name)


async def _ollama_request(
    payload: dict[str, Any],
    *,
    cancel_event: asyncio.Event | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """POST a chat payload to the local Ollama server."""
    model = str(payload.get("model") or config.MODEL_NAME)
    url = f"{config.OLLAMA_BASE_URL}/api/chat"
    request_timeout = timeout if timeout is not None else OLLAMA_CHAT_TIMEOUT
    client = httpx.AsyncClient(timeout=request_timeout)
    try:
        post_task = asyncio.create_task(client.post(url, json=payload))
        if cancel_event is not None:
            cancel_wait = asyncio.create_task(cancel_event.wait())
            done, _pending = await asyncio.wait(
                {post_task, cancel_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_wait in done:
                await _join_cancelled(post_task)
                await client.aclose()
                raise FileCancelledError("File processing was cancelled")
            cancel_wait.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cancel_wait
        resp = await post_task
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError as exc:
        raise RuntimeError(
            f"Cannot reach Ollama at {config.OLLAMA_BASE_URL} — is `ollama serve` running?"
        ) from exc
    except httpx.TimeoutException as exc:
        raise RuntimeError(
            f"Ollama request timed out after {request_timeout:.0f}s"
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(format_http_error(exc, model=model, kind="model")) from exc
    finally:
        with contextlib.suppress(Exception):
            await client.aclose()


async def _complete_ollama(
    prompt: str,
    *,
    instructions: str,
    model_name: str,
    images: list[bytes] | None = None,
    options: dict[str, Any] | None = None,
    cancel_event: asyncio.Event | None = None,
    timeout: float | None = None,
) -> str:
    """Text or multimodal completion via a local Ollama server."""
    ensure_ollama_ready()
    # Config may say "gemma3" while the local tag is "gemma3:4b".
    resolved = resolve_runtime_model(model_name)
    user_message: dict[str, Any] = {"role": "user", "content": prompt}
    if images:
        user_message["images"] = [
            base64.b64encode(raw).decode("ascii") for raw in images
        ]
    ollama_opts: dict[str, Any] = {"temperature": 0.2}
    if options:
        ollama_opts.update(options)
    data = await _ollama_request(
        {
            "model": resolved,
            "messages": [
                {"role": "system", "content": instructions},
                user_message,
            ],
            "stream": False,
            "options": ollama_opts,
        },
        cancel_event=cancel_event,
        timeout=timeout,
    )
    prompt_tok, completion_tok, total = normalize_ollama_usage(data)
    record_usage(
        "ollama",
        resolved,
        prompt_tokens=prompt_tok,
        completion_tokens=completion_tok,
        total_tokens=total,
        kind="chat",
    )
    return ((data.get("message") or {}).get("content") or "").strip()


async def _complete_openai_api(
    prompt: str,
    *,
    instructions: str,
    model_name: str,
    json_mode: bool = False,
) -> str:
    from paperless_agent.auth import ensure_openai_env

    ensure_openai_env()
    client = AsyncOpenAI(timeout=LLM_TEXT_TIMEOUT, max_retries=2)
    response = await client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": instructions},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        **({"response_format": {"type": "json_object"}} if json_mode else {}),
    )
    _record_openai_response("openai", model_name, response)
    return (response.choices[0].message.content or "").strip()


async def _complete_gemini(prompt: str, *, instructions: str, model_name: str) -> str:
    from google import genai

    from paperless_agent.config import GOOGLE_API_KEY

    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY is not set")

    def _call() -> str:
        client = genai.Client(api_key=GOOGLE_API_KEY)
        response = client.models.generate_content(
            model=model_name,
            contents=f"{instructions}\n\n{prompt}",
        )
        _record_gemini_response(model_name, response)
        return (getattr(response, "text", None) or "").strip()

    # Sync Gemini client would otherwise freeze the whole asyncio event loop.
    return await asyncio.to_thread(_call)


async def complete_with_images(
    prompt: str,
    *,
    images: list[bytes],
    instructions: str,
    mime_type: str = "image/png",
    cancel_event: asyncio.Event | None = None,
    timeout: float | None = None,
    ollama_options: dict[str, Any] | None = None,
) -> str:
    """
    Multimodal completion with one or more page images (PNG/JPEG bytes).

    Used for AI vision OCR of scanned page images.
    """
    if not images:
        raise ValueError("images must not be empty")

    model_name = resolve_model_name()
    if config.LLM_PROVIDER == "ollama":
        effective_timeout = (
            timeout if timeout is not None else max(LLM_VISION_TIMEOUT, OLLAMA_CHAT_TIMEOUT)
        )
        coro = _complete_ollama(
            prompt,
            instructions=instructions,
            model_name=model_name,
            images=images,
            options=ollama_options,
            cancel_event=cancel_event,
            timeout=effective_timeout,
        )
    elif config.LLM_PROVIDER == "openai":
        mode = resolve_auth_mode()
        if mode == "chatgpt_oauth":
            coro = _complete_codex_images(
                prompt,
                images=images,
                instructions=instructions,
                model_name=model_name,
                mime_type=mime_type,
            )
        else:
            coro = _complete_openai_images(
                prompt,
                images=images,
                instructions=instructions,
                model_name=model_name,
                mime_type=mime_type,
            )
        effective_timeout = timeout if timeout is not None else LLM_VISION_TIMEOUT
    else:
        coro = _complete_gemini_images(
            prompt,
            images=images,
            instructions=instructions,
            model_name=model_name,
            mime_type=mime_type,
        )
        effective_timeout = timeout if timeout is not None else LLM_VISION_TIMEOUT
    try:
        return await run_cancellable(
            coro, cancel_event=cancel_event, timeout=effective_timeout
        )
    except TimeoutError as exc:
        raise RuntimeError(
            f"LLM vision completion timed out after {effective_timeout:.0f}s"
        ) from exc


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
        timeout=LLM_VISION_TIMEOUT,
        max_retries=2,
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
    return await _collect_codex_stream(stream, model_name)


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

    client = AsyncOpenAI(timeout=LLM_VISION_TIMEOUT, max_retries=2)
    response = await client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": instructions},
            {"role": "user", "content": content},
        ],
        temperature=0.1,
    )
    _record_openai_response("openai", model_name, response)
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

    def _call() -> str:
        client = genai.Client(api_key=GOOGLE_API_KEY)
        parts: list[Any] = [types.Part.from_text(text=f"{instructions}\n\n{prompt}")]
        for raw in images:
            parts.append(types.Part.from_bytes(data=raw, mime_type=mime_type))
        response = client.models.generate_content(
            model=model_name,
            contents=parts,
        )
        _record_gemini_response(model_name, response)
        return (getattr(response, "text", None) or "").strip()

    return await asyncio.to_thread(_call)
