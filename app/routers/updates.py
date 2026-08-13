"""In-app software update routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from paperless_agent.inbox_worker import is_processing
from paperless_agent.updater import (
    UPDATE_REPO,
    apply_update,
    check_for_update,
    schedule_restart,
)
from paperless_agent.version import get_current_version

router = APIRouter(tags=["updates"])


@router.get("/api/update/status")
def api_update_status(check: bool = Query(default=False)) -> dict[str, Any]:
    """Installed version; with check=true also queries GitHub for the latest release."""
    if not check:
        return {
            "status": "success",
            "repo": UPDATE_REPO,
            "current_version": get_current_version(),
        }
    return check_for_update()


@router.post("/api/update/apply")
def api_update_apply() -> dict[str, Any]:
    """Download the latest GitHub release and install it over this instance."""
    result = apply_update()
    if result.get("status") != "success":
        raise HTTPException(status_code=409, detail=result.get("error", "update failed"))
    return result


@router.post("/api/update/restart")
def api_update_restart() -> dict[str, Any]:
    """Restart the server process (used after installing an update)."""
    if is_processing():
        raise HTTPException(
            status_code=409,
            detail="documents are being processed — try again in a moment",
        )
    return schedule_restart()
