"""FastAPI app: upload, list/search documents, and RAG ask."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from paperless_agent.auth import codex_auth_status
from paperless_agent.codex_oauth import (
    clear_auth,
    complete_oauth_login_manual,
    poll_oauth_login,
    save_api_key,
    start_oauth_login,
)
from paperless_agent.config import EMBEDDING_MODEL, LLM_PROVIDER, MODEL_NAME, ensure_data_dirs
from paperless_agent.inbox_worker import inbox_poll_loop, process_inbox
from paperless_agent.llm import resolve_model_name
from paperless_agent.progress import PIPELINE_STEPS, subscribe
from paperless_agent.review import (
    approve_review,
    get_review,
    list_pending,
    reject_review,
)
from paperless_agent.runner import run_pipeline_on_path, run_query
from paperless_agent.settings import (
    load_settings,
    save_settings,
    validate_path,
)
from paperless_agent.tools.filesystem import (
    clear_inbox,
    list_inbox,
    reveal_in_explorer,
    save_upload_to_inbox,
)
from paperless_agent.tools.metadata_db import get_document, list_recent, search_metadata
from paperless_agent.tools.rag_index import retrieve_chunks
from paperless_agent.tools.storage import clear_all_stored_data
from paperless_agent.updater import (
    UPDATE_REPO,
    apply_update,
    check_for_update,
    get_current_version,
    schedule_restart,
)


STATIC_DIR = Path(__file__).resolve().parent / "static"
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_data_dirs()
    load_settings()
    stop_event = asyncio.Event()
    poller = asyncio.create_task(inbox_poll_loop(stop_event), name="inbox-poller")
    logger.info("Started inbox poller task")
    try:
        yield
    finally:
        stop_event.set()
        poller.cancel()
        try:
            await poller
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="PaperlessAgent",
    description="Local ADK document ingest + RAG query API",
    version="0.1.0",
    lifespan=lifespan,
)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)


class ProcessRequest(BaseModel):
    path: str = Field(..., min_length=1)


class ApiKeyRequest(BaseModel):
    api_key: str = Field(..., min_length=8)


class OAuthCompleteRequest(BaseModel):
    state: str = Field(..., min_length=8)
    callback: str = Field(..., min_length=1)


class CategorySetting(BaseModel):
    name: str = Field(..., min_length=1)
    folder: str = Field(..., min_length=1)


class BatchSetting(BaseModel):
    poll_interval_seconds: float = Field(
        default=30,
        ge=0,
        description="How often to scan the inbox for new files. 0 disables auto-scan.",
    )


class ReviewSetting(BaseModel):
    require_approval: bool = Field(
        default=True,
        description="Hold proposed filings for human approval before writing.",
    )


class SettingsRequest(BaseModel):
    source_dir: str = Field(..., min_length=1)
    categories: list[CategorySetting] = Field(..., min_length=1)
    batch: BatchSetting = Field(default_factory=BatchSetting)
    review: ReviewSetting = Field(default_factory=ReviewSetting)


class ReviewApproveRequest(BaseModel):
    filename: str | None = None
    doc_type: str | None = None
    doc_date: str | None = None
    counterparties: str | None = None
    amount: float | None = None
    currency: str | None = None
    summary: str | None = None


class ReviewRejectRequest(BaseModel):
    delete_file: bool = Field(
        default=True,
        description="Also remove the scan from the inbox so it is not reprocessed.",
    )


class ValidatePathRequest(BaseModel):
    path: str = Field(..., min_length=1)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "llm_provider": LLM_PROVIDER,
        "model": resolve_model_name(),
        "configured_model": MODEL_NAME,
        "embedding_model": EMBEDDING_MODEL,
        "auth": codex_auth_status(),
    }


@app.get("/api/auth/status")
def api_auth_status() -> dict[str, Any]:
    return {"status": "success", **codex_auth_status()}


@app.post("/api/auth/openai/start")
def api_auth_openai_start() -> dict[str, Any]:
    try:
        return start_oauth_login()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/auth/openai/poll")
def api_auth_openai_poll(state: str = Query(..., min_length=8)) -> dict[str, Any]:
    return poll_oauth_login(state)


@app.post("/api/auth/openai/complete")
def api_auth_openai_complete(body: OAuthCompleteRequest) -> dict[str, Any]:
    try:
        return complete_oauth_login_manual(state=body.state, raw=body.callback)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/auth/api-key")
def api_auth_api_key(body: ApiKeyRequest) -> dict[str, Any]:
    try:
        return save_api_key(body.api_key)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/auth/logout")
def api_auth_logout() -> dict[str, Any]:
    return clear_auth()


@app.get("/api/settings")
def api_get_settings() -> dict[str, Any]:
    return {"status": "success", "settings": load_settings()}


@app.put("/api/settings")
def api_put_settings(body: SettingsRequest) -> dict[str, Any]:
    try:
        saved = save_settings(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "settings": saved}


@app.post("/api/settings/validate-path")
def api_validate_path(body: ValidatePathRequest) -> dict[str, Any]:
    return validate_path(body.path)


@app.get("/api/inbox")
def api_inbox() -> dict[str, Any]:
    return list_inbox()


@app.delete("/api/inbox")
def api_clear_inbox() -> dict[str, Any]:
    return clear_inbox()


@app.delete("/api/data")
def api_clear_all_data() -> dict[str, Any]:
    """Delete archived files, inbox scans, SQLite metadata, and Chroma RAG data."""
    try:
        return clear_all_stored_data()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename required")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    saved = save_upload_to_inbox(file.filename, content)
    return saved


@app.post("/api/process")
async def api_process(body: ProcessRequest) -> dict[str, Any]:
    path = Path(body.path).expanduser().resolve()
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"file not found: {body.path}")
    try:
        return await run_pipeline_on_path(str(path))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/process-inbox")
async def api_process_inbox() -> dict[str, Any]:
    return await process_inbox()


@app.get("/api/process/pipeline")
def api_process_pipeline() -> dict[str, Any]:
    """Static pipeline step definitions for the workflow UI."""
    return {"status": "success", "steps": list(PIPELINE_STEPS)}


@app.get("/api/process/events")
async def api_process_events() -> StreamingResponse:
    """Server-Sent Events stream of live ingest progress."""

    async def event_stream():
        # Hello + schema so the UI can render an idle stepper immediately.
        hello = json.dumps(
            {"type": "hello", "steps": list(PIPELINE_STEPS)},
            ensure_ascii=False,
        )
        yield f"data: {hello}\n\n"
        async for event in subscribe(replay=True):
            payload = json.dumps(event, ensure_ascii=False, default=str)
            yield f"data: {payload}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/update/status")
def api_update_status(check: bool = Query(default=False)) -> dict[str, Any]:
    """Installed version; with check=true also queries GitHub for the latest release."""
    if not check:
        return {
            "status": "success",
            "repo": UPDATE_REPO,
            "current_version": get_current_version(),
        }
    return check_for_update()


@app.post("/api/update/apply")
def api_update_apply() -> dict[str, Any]:
    """Download the latest GitHub release and install it over this instance."""
    result = apply_update()
    if result.get("status") != "success":
        raise HTTPException(status_code=409, detail=result.get("error", "update failed"))
    return result


@app.post("/api/update/restart")
def api_update_restart() -> dict[str, Any]:
    """Restart the server process (used after installing an update)."""
    return schedule_restart()


@app.get("/api/reviews")
def api_reviews() -> dict[str, Any]:
    """Pending human-in-the-loop review items."""
    return list_pending()


@app.post("/api/reviews/{review_id}/approve")
def api_approve_review(review_id: str, body: ReviewApproveRequest) -> dict[str, Any]:
    """Approve a pending filing, optionally with human corrections."""
    overrides = {k: v for k, v in body.model_dump().items() if v is not None}
    result = approve_review(review_id, overrides)
    if result.get("status") not in {"success", "partial"}:
        raise HTTPException(status_code=409, detail=result.get("error", "approve failed"))
    return result


@app.post("/api/reviews/{review_id}/reject")
def api_reject_review(review_id: str, body: ReviewRejectRequest) -> dict[str, Any]:
    """Reject a pending filing; by default also removes the inbox scan."""
    result = reject_review(review_id, delete_file=body.delete_file)
    if result.get("status") != "success":
        raise HTTPException(status_code=409, detail=result.get("error", "reject failed"))
    return result


@app.get("/api/reviews/{review_id}/file")
@app.head("/api/reviews/{review_id}/file")
def api_review_file(review_id: str) -> FileResponse:
    """Serve the original scan of a pending review for in-browser viewing."""
    review = get_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail=f"review not found: {review_id}")
    path = Path(review["source_path"]).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="source scan is missing on disk")
    guessed, _ = mimetypes.guess_type(str(path))
    media_type = "application/pdf" if path.suffix.lower() == ".pdf" else (
        guessed or "application/octet-stream"
    )
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type="inline",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@app.get("/api/documents")
def api_documents(
    q: str | None = None,
    doc_type: str | None = None,
    counterparty: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    if q or doc_type or counterparty:
        return search_metadata(
            query=q,
            doc_type=doc_type,
            counterparty=counterparty,
            limit=limit,
        )
    return list_recent(limit=limit)


@app.get("/api/documents/{document_id}")
def api_document(document_id: str) -> dict[str, Any]:
    result = get_document(document_id)
    if result.get("status") != "success":
        raise HTTPException(status_code=404, detail=result.get("error", "not found"))
    return result


@app.post("/api/documents/{document_id}/reveal")
def api_reveal_document(document_id: str) -> dict[str, Any]:
    """Reveal an archived document in the system file manager."""
    result = get_document(document_id)
    if result.get("status") != "success":
        raise HTTPException(status_code=404, detail=result.get("error", "not found"))
    path = (result.get("document") or {}).get("path")
    if not path:
        raise HTTPException(status_code=404, detail="document has no path")
    revealed = reveal_in_explorer(path)
    if revealed.get("status") != "success":
        raise HTTPException(status_code=500, detail=revealed.get("error", "reveal failed"))
    return revealed


@app.get("/api/documents/{document_id}/file")
@app.head("/api/documents/{document_id}/file")
def api_document_file(document_id: str) -> FileResponse:
    """Serve an archived document file for in-browser viewing."""
    result = get_document(document_id)
    if result.get("status") != "success":
        raise HTTPException(status_code=404, detail=result.get("error", "not found"))
    document = result.get("document") or {}
    path_value = document.get("path")
    if not path_value:
        raise HTTPException(status_code=404, detail="document has no path")
    path = Path(path_value).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                "File is missing on disk. It may have been deleted or filed to a "
                "temporary path. Re-process the scan, or use Remove all stored data "
                "and ingest again."
            ),
        )
    guessed, _ = mimetypes.guess_type(str(path))
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        media_type = "application/pdf"
    elif guessed:
        media_type = guessed
    else:
        media_type = "application/octet-stream"
    headers = {
        # Prefer inline viewing; avoid attachment downloads for PDFs/images.
        "Content-Disposition": (
            f'inline; filename="{document.get("filename") or path.name}"'
        ),
        "X-Content-Type-Options": "nosniff",
    }
    return FileResponse(
        path,
        media_type=media_type,
        filename=document.get("filename") or path.name,
        content_disposition_type="inline",
        headers=headers,
    )


@app.get("/api/retrieve")
def api_retrieve(
    q: str = Query(..., min_length=1),
    top_k: int = Query(default=6, ge=1, le=20),
    doc_type: str | None = None,
) -> dict[str, Any]:
    return retrieve_chunks(query=q, top_k=top_k, doc_type=doc_type)


@app.post("/api/ask")
async def api_ask(body: AskRequest) -> dict[str, Any]:
    try:
        return await run_query(body.question)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/settings")
def settings_page() -> RedirectResponse:
    """Settings now lives inside the app shell."""
    return RedirectResponse(url="/#/settings")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
