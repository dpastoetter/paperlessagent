"""Processing API: cancel, retry, pipeline schema, SSE events."""

from __future__ import annotations

import asyncio
import json

from deepcatalog import inbox_worker
from deepcatalog.progress import PIPELINE_STEPS
from deepcatalog.settings import get_source_dir


def test_process_pipeline_endpoint(client):
    resp = client.get("/api/process/pipeline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert isinstance(body["steps"], list)
    assert len(body["steps"]) == len(PIPELINE_STEPS)
    assert body["steps"][0]["id"] == PIPELINE_STEPS[0]["id"]


def test_process_events_sse_hello():
    """Read the hello frame without leaving an open SSE subscribe loop."""

    async def first_chunk() -> str:
        from app.routers.processing import api_process_events

        response = await api_process_events()
        assert "text/event-stream" in response.media_type
        chunk = await response.body_iterator.__anext__()
        if isinstance(chunk, bytes):
            return chunk.decode("utf-8")
        return str(chunk)

    chunk = asyncio.run(first_chunk())
    assert chunk.startswith("data: ")
    payload = json.loads(chunk.removeprefix("data: ").strip())
    assert payload["type"] == "hello"
    assert isinstance(payload["steps"], list)


def test_process_cancel_when_idle(client):
    resp = client.post("/api/process/cancel", json={"file_id": "missing"})
    assert resp.status_code == 409
    assert "no ingest" in resp.json()["detail"].lower()


def test_process_cancel_and_retry_http_success(client, monkeypatch):
    async def cancel_ok(file_id: str):
        return {
            "status": "success",
            "file_id": file_id,
            "message": "Cancellation requested",
        }

    async def retry_ok(path: str):
        return {"status": "success", "path": path}

    monkeypatch.setattr("app.routers.processing.cancel_active_file", cancel_ok)
    monkeypatch.setattr("app.routers.processing.retry_file", retry_ok)

    cancel = client.post("/api/process/cancel", json={"file_id": "fid-1"})
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "success"

    retry = client.post("/api/process/retry", json={"path": "/tmp/in.pdf"})
    assert retry.status_code == 200
    assert retry.json()["status"] == "success"


def test_process_retry_and_cancel_active(client, monkeypatch):
    pdf = get_source_dir() / "retry_me.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")
    started = asyncio.Event()
    release = asyncio.Event()
    active_ids: list[str] = []

    async def slow_pipeline(path: str):
        from deepcatalog.job_control import get_active_file_id

        active_ids.append(get_active_file_id() or "")
        started.set()
        await release.wait()
        return {"status": "success", "path": path}

    monkeypatch.setattr(inbox_worker, "run_pipeline_on_path", slow_pipeline)

    async def run_job():
        return await inbox_worker.process_single_file(str(pdf))

    async def scenario():
        task = asyncio.create_task(run_job())
        await started.wait()
        file_id = active_ids[0]
        assert file_id

        # Drive cancel through the HTTP API while the worker holds the lock.
        # TestClient is sync; call the router coroutine helpers instead via worker.
        cancel = await inbox_worker.cancel_active_file(file_id)
        assert cancel["status"] == "success"

        retry = await inbox_worker.retry_file(str(pdf))
        assert retry["status"] == "success"
        assert retry.get("cancelled") is True

        release.set()
        await task

    asyncio.run(scenario())

    async def fake_ok(path: str):
        return {"status": "success", "path": path}

    monkeypatch.setattr(inbox_worker, "run_pipeline_on_path", fake_ok)
    resp = client.post("/api/process/retry", json={"path": str(pdf)})
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


def test_process_single_path_outside_inbox(client, isolated_data):
    outside = isolated_data.parent / "escape.pdf"
    outside.write_bytes(b"%PDF escape")
    resp = client.post("/api/process", json={"path": str(outside)})
    assert resp.status_code == 400
    assert "inbox" in resp.json()["detail"].lower()
