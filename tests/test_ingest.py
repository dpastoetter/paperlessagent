"""Tests for ingest extraction normalization (no live LLM)."""

from __future__ import annotations

from paperless_agent.ingest import normalize_extracted_fields


def test_normalize_maps_parties_to_counterparties():
    raw = {
        "doc_type": "letter",
        "parties": "Jane Doe, John Smith",
        "subject": "Rent increase notice",
        "amount": "",
        "currency": "",
    }
    out = normalize_extracted_fields(raw)
    assert out["parties"] == "Jane Doe, John Smith"
    assert out["counterparties"] == "Jane Doe, John Smith"
    assert out["subject"] == "Rent increase notice"
    assert out["amount"] is None
    assert out["currency"] is None


def test_normalize_accepts_legacy_counterparties_key():
    raw = {
        "doc_type": "invoice",
        "counterparties": "Acme GmbH",
        "amount": "120.5",
        "currency": "eur",
    }
    out = normalize_extracted_fields(raw)
    assert out["counterparties"] == "Acme GmbH"
    assert out["amount"] == 120.5
    assert out["currency"] == "EUR"


def test_normalize_reference_ids_from_legacy_ids():
    raw = {
        "doc_type": "insurance",
        "ids": ["POL-123", "CASE-9"],
        "reference_ids": None,
    }
    out = normalize_extracted_fields(raw)
    assert out["reference_ids"] == ["POL-123", "CASE-9"]


def test_normalize_clears_currency_when_no_amount():
    raw = {
        "doc_type": "medical",
        "parties": "Dr. Weber",
        "amount": None,
        "currency": "EUR",
    }
    out = normalize_extracted_fields(raw)
    assert out["amount"] is None
    assert out["currency"] is None


def test_normalize_clears_amount_for_non_financial_doc_type():
    raw = {
        "doc_type": "other",
        "subject": "Chess board mid-game position",
        "amount": 99.99,
        "currency": "EUR",
    }
    out = normalize_extracted_fields(raw)
    assert out["amount"] is None
    assert out["currency"] is None


def test_normalize_keeps_amount_for_invoice():
    raw = {
        "doc_type": "invoice",
        "parties": "Acme Corp",
        "amount": "380.0",
        "currency": "eur",
    }
    out = normalize_extracted_fields(raw)
    assert out["amount"] == 380.0
    assert out["currency"] == "EUR"


def test_normalize_defaults_doc_type_to_other():
    raw = {
        "subject": "Unknown scan",
        "summary": "Could not classify.",
    }
    out = normalize_extracted_fields(raw)
    assert out["doc_type"] == "other"


def test_normalize_accepts_document_type_alias():
    raw = {
        "document_type": "Medical",
        "parties": "Dr. Smith",
    }
    out = normalize_extracted_fields(raw)
    assert out["doc_type"] == "medical"


def test_text_for_extract_prompt_uses_head_and_tail():
    from paperless_agent.ingest import _text_for_extract_prompt

    text = "A" * 100 + "MIDDLE" + "B" * 100
    sampled = _text_for_extract_prompt(text, max_chars=120)
    assert "[... middle omitted ...]" in sampled
    assert "MIDDLE" not in sampled
    assert sampled.startswith("(Document is ")
    assert "A" * 10 in sampled
    assert "B" * 10 in sampled


def test_text_for_extract_prompt_short_text_unchanged():
    from paperless_agent.ingest import _text_for_extract_prompt

    text = "Short letter about rent."
    assert _text_for_extract_prompt(text, max_chars=48000) == text
