"""Filing settings, path validation, and autostart routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.schemas import AutostartRequest, SettingsRequest, ValidatePathRequest
from paperless_agent.settings import (
    SettingsError,
    load_settings,
    save_settings,
    validate_path,
)
from paperless_agent.system_service import autostart_status, set_autostart
from paperless_agent.tools.storage import clear_all_stored_data

router = APIRouter(tags=["settings"])


@router.get("/api/settings")
def api_get_settings() -> dict[str, Any]:
    try:
        settings = load_settings()
    except SettingsError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"status": "success", "settings": settings}


@router.put("/api/settings")
def api_put_settings(body: SettingsRequest) -> dict[str, Any]:
    try:
        saved = save_settings(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "settings": saved}


@router.post("/api/settings/validate-path")
def api_validate_path(body: ValidatePathRequest) -> dict[str, Any]:
    return validate_path(body.path)


@router.get("/api/autostart/status")
def api_autostart_status() -> dict[str, Any]:
    return {"status": "success", "autostart": autostart_status()}


@router.post("/api/autostart")
def api_autostart(body: AutostartRequest) -> dict[str, Any]:
    try:
        return set_autostart(body.enabled)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/api/data")
def api_clear_all_data() -> dict[str, Any]:
    """Delete archived files, inbox scans, SQLite metadata, and Chroma RAG data."""
    try:
        return clear_all_stored_data()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
