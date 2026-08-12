"""Tests for the ingest progress bus."""

from __future__ import annotations

import asyncio

from paperless_agent.progress import (
    PIPELINE_STEPS,
    _buffer,
    emit_step,
    llm_busy_detail,
    publish,
    step_label,
    subscribe,
)


def test_step_labels_are_human_readable():
    assert step_label("ai_ocr") == "Transcribe"
    assert step_label("extract") == "Find details"
    assert [s["id"] for s in PIPELINE_STEPS] == [
        "read",
        "ai_ocr",
        "extract",
        "name",
        "review",
        "file",
        "index",
    ]
    assert all(s.get("description") for s in PIPELINE_STEPS)
    detail = llm_busy_detail("Reading 2 page images")
    assert "Reading 2 page images" in detail


def test_progress_publish_and_subscribe():
    # The ring buffer is module-level and survives across tests; drop events
    # replayed from other test files so this test only sees its own.
    _buffer.clear()

    async def run():
        await publish({"type": "job_started", "job_id": "j1", "total": 1})
        await emit_step("read", label="Open file", status="running", filename="a.pdf")

        events = []

        async def collector():
            async for event in subscribe(replay=True):
                events.append(event)
                if event.get("type") == "step" and event.get("status") == "done":
                    break

        task = asyncio.create_task(collector())
        await asyncio.sleep(0.05)
        await emit_step("read", label="Open file", status="done", filename="a.pdf")
        await asyncio.wait_for(task, timeout=2)
        types = [e.get("type") for e in events]
        assert "job_started" in types
        assert "step" in types
        assert any(e.get("status") == "done" for e in events if e.get("type") == "step")

    asyncio.run(run())
