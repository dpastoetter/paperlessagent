"""Filing settings, path validation, and autostart routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.schemas import AutostartRequest, ClearDataRequest, SettingsRequest, ValidatePathRequest
from paperless_agent.settings import (
    SettingsError,
    load_settings,
    save_settings,
    validate_path,
)
from paperless_agent.system_service import autostart_status, set_autostart
from paperless_agent.tools.storage import CLEAR_DATA_CONFIRMATION, clear_all_stored_data

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
def api_clear_all_data(body: ClearDataRequest) -> dict[str, Any]:
    """
    Delete tracked archive files (path-confined), supported inbox scans, SQLite, and Chroma.

    Requires the exact confirmation phrase — browser two-click UX is not sufficient.
    """
    if body.confirmation != CLEAR_DATA_CONFIRMATION:
        raise HTTPException(
            status_code=400,
            detail=(
                f'confirmation must be exactly "{CLEAR_DATA_CONFIRMATION}" '
                "(client UI confirmations are not a security boundary)"
            ),
        )
    try:
        return clear_all_stored_data()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
