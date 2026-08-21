"""Tests for deterministic archive Q&A."""

from __future__ import annotations

import asyncio

import pytest

from paperless_agent.ask import ask_archive, filter_confident_chunks
from paperless_agent.config import ensure_data_dirs
from paperless_agent.settings import clear_settings_cache, load_settings
from paperless_agent.tools import metadata_db, rag_index


@pytest.fixture()
def isolated_ask(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr("paperless_agent.config.DATA_DIR", data)
    monkeypatch.setattr("paperless_agent.config.INBOX_DIR", data / "inbox")
    monkeypatch.setattr("paperless_agent.config.ARCHIVE_DIR", data / "archive")
    monkeypatch.setattr("paperless_agent.config.DB_PATH", data / "paperless.db")
    monkeypatch.setattr("paperless_agent.config.CHROMA_DIR", data / "chroma")
    monkeypatch.setattr("paperless_agent.tools.metadata_db.DB_PATH", data / "paperless.db")
    monkeypatch.setattr("paperless_agent.tools.rag_index.CHROMA_DIR", data / "chroma")
    clear_settings_cache()
    ensure_data_dirs()
    load_settings()

    def fake_embed(texts):
        vectors = []
        for t in texts:
            vec = [0.0] * 8
            for i, ch in enumerate(t.encode("utf-8")):
                vec[i % 8] += (ch % 17) / 17.0
            vectors.append(vec)
        return vectors

    monkeypatch.setattr(rag_index, "embed_texts", fake_embed)

    archive = data / "archive" / "invoice" / "2022" / "invoice.pdf"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"%PDF-1.4 stub")
    saved = metadata_db.upsert_metadata(
        original_name="scan.pdf",
        filename="2022-09-05_Invoice_BV_CRE8_EUR181p50.pdf",
        path=str(archive),
        doc_type="invoice",
        doc_date="2022-09-05",
        counterparties="BV CRE8",
        amount=181.5,
        currency="EUR",
        summary="Invoice FA2022-0001 from BV CRE8 for €181.50.",
    )
    doc_id = saved["document_id"]
    rag_index.index_document(
        document_id=doc_id,
        text=(
            "Invoice FA2022-0001 from BV CRE8 dated 2022-09-05. "
            "Total incl VAT €181.50 for comanage business package."
        ),
        filename="2022-09-05_Invoice_BV_CRE8_EUR181p50.pdf",
        doc_type="invoice",
    )
    yield {"doc_id": doc_id, "data": data}
    clear_settings_cache()


def test_ask_archive_uses_evidence(isolated_ask, monkeypatch):
    async def fake_complete(prompt: str, *, instructions: str) -> str:
        assert "User question" in prompt
        assert "BV CRE8" in prompt or "181" in prompt
        assert "BEGIN_UNTRUSTED_EVIDENCE" in prompt
        assert "END_UNTRUSTED_EVIDENCE" in prompt
        assert "untrusted" in instructions.lower()
        assert "recent documents" not in prompt.lower()
        assert "archive assistant" in instructions.lower() or "paperless" in instructions.lower()
        return (
            f"You have one invoice from BV CRE8 for €181.50 (document_id={isolated_ask['doc_id']})."
        )

    monkeypatch.setattr("paperless_agent.ask.complete_text", fake_complete)
    result = asyncio.run(ask_archive("What invoices do I have and for how much?"))
    assert result["status"] == "success"
    assert result.get("grounded") is True
    assert result.get("evidence") == "strong"
    assert "181" in result["reply"]
    assert result["sources"]
    assert result["sources"][0]["document_id"] == isolated_ask["doc_id"]
    assert result["sources"][0]["open_url"] == (f"/api/documents/{isolated_ask['doc_id']}/file")
    assert result["sources"][0].get("doc_date") == "2022-09-05"
    assert result["sources"][0].get("snippet")
    assert "181" in result["sources"][0]["snippet"] or "CRE8" in result["sources"][0]["snippet"]


def test_ask_archive_empty_question():
    result = asyncio.run(ask_archive("   "))
    assert result["status"] == "error"


def test_ask_does_not_pad_with_recent_docs(isolated_ask, monkeypatch):
    """Unrelated questions must not inject recent archive docs into the prompt."""
    llm_calls: list[str] = []

    async def fake_complete(prompt: str, *, instructions: str) -> str:
        llm_calls.append(prompt)
        return "should not be called"

    monkeypatch.setattr("paperless_agent.ask.complete_text", fake_complete)
    monkeypatch.setattr(
        "paperless_agent.ask.retrieve_chunks",
        lambda _q: {
            "status": "success",
            "chunks": [
                {
                    "text": "weather patterns over oceans",
                    "document_id": "unrelated",
                    "filename": "noise.pdf",
                    "doc_type": "other",
                    "distance": 0.92,
                }
            ],
        },
    )
    monkeypatch.setattr(
        "paperless_agent.ask.search_metadata",
        lambda **_kwargs: {"status": "success", "documents": [], "count": 0},
    )

    result = asyncio.run(ask_archive("What is the weather in Paris tomorrow?"))
    assert result["status"] == "success"
    assert result.get("grounded") is False
    assert result.get("evidence") == "none"
    assert "enough evidence" in result["reply"].lower()
    assert result["sources"] == []
    assert llm_calls == []


def test_filter_confident_chunks_respects_distance():
    chunks = [
        {"text": "a", "distance": 0.2},
        {"text": "b", "distance": 0.7},
        {"text": "c", "distance": None},
    ]
    kept = filter_confident_chunks(chunks, max_distance=0.55)
    assert [c["text"] for c in kept] == ["a"]


def test_ask_uses_metadata_keyword_hits_without_chunks(isolated_ask, monkeypatch):
    async def fake_complete(prompt: str, *, instructions: str) -> str:
        assert "FA2022-0001" in prompt or "BV CRE8" in prompt
        return "Invoice FA2022-0001 is from BV CRE8."

    monkeypatch.setattr("paperless_agent.ask.complete_text", fake_complete)
    monkeypatch.setattr(
        "paperless_agent.ask.retrieve_chunks",
        lambda _q: {"status": "success", "chunks": []},
    )
    result = asyncio.run(ask_archive("Find invoice FA2022-0001"))
    assert result["status"] == "success"
    assert result.get("grounded") is True
    assert result.get("evidence") == "weak"
    assert result["metadata_count"] >= 1
    assert result["sources"]
    assert result["sources"][0].get("doc_date") == "2022-09-05"
    assert result["sources"][0].get("snippet")


def test_ask_history_is_prompt_only_retrieval_uses_question(isolated_ask, monkeypatch):
    seen_queries: list[str] = []

    async def fake_complete(prompt: str, *, instructions: str) -> str:
        assert "Recent conversation" in prompt
        assert "earlier topic" in prompt
        assert "User question:\nHow much was that invoice?" in prompt
        return "The invoice was €181.50."

    def fake_retrieve(q: str):
        seen_queries.append(q)
        return {
            "status": "success",
            "chunks": [
                {
                    "text": "Invoice FA2022-0001 total €181.50",
                    "document_id": isolated_ask["doc_id"],
                    "filename": "2022-09-05_Invoice_BV_CRE8_EUR181p50.pdf",
                    "doc_type": "invoice",
                    "distance": 0.2,
                }
            ],
        }

    monkeypatch.setattr("paperless_agent.ask.complete_text", fake_complete)
    monkeypatch.setattr("paperless_agent.ask.retrieve_chunks", fake_retrieve)

    result = asyncio.run(
        ask_archive(
            "How much was that invoice?",
            history=[
                {"role": "user", "content": "Tell me about earlier topic"},
                {"role": "assistant", "content": "Earlier answer about CRE8."},
            ],
        )
    )
    assert result["status"] == "success"
    assert seen_queries == ["How much was that invoice?"]
    assert result.get("evidence") == "strong"


def test_ask_history_clamped(isolated_ask, monkeypatch):
    async def fake_complete(prompt: str, *, instructions: str) -> str:
        # Oldest turns should be dropped (keep last 6 role messages).
        assert "turn-0" not in prompt
        assert "turn-7" in prompt or "turn-5" in prompt
        return "ok"

    monkeypatch.setattr("paperless_agent.ask.complete_text", fake_complete)
    history = []
    for i in range(10):
        history.append({"role": "user", "content": f"turn-{i} user"})
        history.append({"role": "assistant", "content": f"turn-{i} assistant"})

    result = asyncio.run(ask_archive("Follow up?", history=history))
    assert result["status"] == "success"
