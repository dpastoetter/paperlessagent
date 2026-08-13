"""Stubbed non-Ollama LLM completion branches."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from paperless_agent.llm import complete_text_via_backend


def test_complete_text_openai_api_path(monkeypatch):
    monkeypatch.setattr("paperless_agent.llm.config.LLM_PROVIDER", "openai")
    monkeypatch.setattr("paperless_agent.llm.resolve_auth_mode", lambda: "api_key")
    monkeypatch.setattr("paperless_agent.llm.resolve_model_name", lambda: "gpt-test")

    class FakeCompletions:
        @staticmethod
        async def create(**_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="hello-api"))],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

        def __init__(self, **_kwargs):
            pass

    monkeypatch.setattr("paperless_agent.llm.AsyncOpenAI", FakeClient)
    monkeypatch.setattr(
        "paperless_agent.llm.resolve_openai_api_key",
        lambda: "sk-test",
    )
    monkeypatch.setattr(
        "paperless_agent.auth.ensure_openai_env",
        lambda: None,
    )

    text = asyncio.run(complete_text_via_backend("ping", instructions="sys", json_mode=False))
    assert text == "hello-api"


def test_complete_text_gemini_path(monkeypatch):
    monkeypatch.setattr("paperless_agent.llm.config.LLM_PROVIDER", "gemini")
    monkeypatch.setattr("paperless_agent.llm.resolve_model_name", lambda: "gemini-test")

    class FakeModels:
        @staticmethod
        def generate_content(**_kwargs):
            return SimpleNamespace(text="hello-gemini", usage_metadata=None)

    class FakeClient:
        models = FakeModels()

        def __init__(self, **_kwargs):
            pass

    monkeypatch.setattr(
        "paperless_agent.llm.genai",
        SimpleNamespace(Client=FakeClient),
        raising=False,
    )
    # complete_gemini imports google.genai inside — patch where used
    import paperless_agent.llm as llm_mod

    async def fake_gemini(prompt, *, instructions, model_name):
        return "hello-gemini"

    monkeypatch.setattr(llm_mod, "_complete_gemini", fake_gemini)
    text = asyncio.run(complete_text_via_backend("ping", instructions="sys"))
    assert text == "hello-gemini"
