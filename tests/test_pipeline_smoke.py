"""Smoke tests for ADK agent wiring and ingest helpers (no live API calls)."""

from paperless_agent.agent import root_agent as pipeline_agent
from paperless_agent.llm import resolve_model_name
from paperless_agent.pipeline.agents import build_pipeline_agent, parse_json_blob
from query_agent.agent import root_agent as query_agent


def test_pipeline_agent_structure():
    agent = build_pipeline_agent()
    assert agent.name == "paperless_ingest"
    assert len(agent.tools) == 3
    assert pipeline_agent.name == "paperless_ingest"


def test_query_agent_tools():
    assert query_agent.name == "paperless_query"
    assert len(query_agent.tools) == 3


def test_parse_json_blob():
    assert parse_json_blob('{"doc_type":"invoice"}')["doc_type"] == "invoice"
    assert parse_json_blob('```json\n{"a":1}\n```')["a"] == 1
    assert parse_json_blob("no json here") == {}


def test_codex_oauth_falls_back_from_gpt41(monkeypatch, tmp_path):
    import base64
    import json

    monkeypatch.setenv("PAPERLESS_LLM_PROVIDER", "openai")
    monkeypatch.setenv("PAPERLESS_MODEL", "gpt-4.1")
    monkeypatch.delenv("PAPERLESS_CODEX_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CODEX_API_KEY", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    payload = (
        base64.urlsafe_b64encode(
            json.dumps({"https://api.openai.com/auth": {"chatgpt_account_id": "acct_1"}}).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    access = f"eyJhbGciOiJub25lIn0.{payload}.sig"
    (tmp_path / "auth.json").write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": access,
                    "refresh_token": "r",
                    "account_id": "acct_1",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("paperless_agent.config.LLM_PROVIDER", "openai")
    monkeypatch.setattr("paperless_agent.config.MODEL_NAME", "gpt-4.1")
    assert resolve_model_name() == "gpt-5.6-luna"
