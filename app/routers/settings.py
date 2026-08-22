"""Filing settings, path validation, and autostart routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from app.schemas import AutostartRequest, ClearDataRequest, SettingsRequest, ValidatePathRequest
from deepcatalog.settings import (
    SettingsError,
    load_settings,
    save_settings,
    validate_path,
)
from deepcatalog.system_service import autostart_status, set_autostart
from deepcatalog.tools.storage import CLEAR_DATA_CONFIRMATION, clear_all_stored_data

logger = logging.getLogger(__name__)
router = APIRouter(tags=["settings"])


@router.get("/api/settings")
def api_get_settings() -> dict[str, Any]:
    try:
        settings = load_settings()
    except SettingsError:
        logger.exception("failed to load settings")
        raise HTTPException(status_code=500, detail="settings file is unreadable") from None
    return {"status": "success", "settings": settings}


@router.put("/api/settings")
def api_put_settings(body: SettingsRequest) -> dict[str, Any]:
    try:
        saved = save_settings(body.model_dump())
    except ValueError:
        logger.info("rejected settings payload")
        raise HTTPException(status_code=400, detail="invalid settings") from None
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
    except RuntimeError:
        logger.exception("autostart update failed")
        raise HTTPException(status_code=503, detail="could not update autostart") from None


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
    except Exception:
        logger.exception("clear stored data failed")
        raise HTTPException(status_code=500, detail="could not clear stored data") from None
