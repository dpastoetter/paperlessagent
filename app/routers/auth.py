"""Auth, cloud disclaimer, LLM provider, and Ollama control routes."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.deps import require_cloud_disclaimer_or_403
from app.schemas import (
    ApiKeyRequest,
    CloudDisclaimerRequest,
    LlmProviderRequest,
    OAuthCompleteRequest,
    OllamaEnableRequest,
    OllamaPullRequest,
    OllamaRestartRequest,
)
from paperless_agent import config
from paperless_agent.auth import codex_auth_status
from paperless_agent.codex_oauth import (
    clear_auth,
    complete_oauth_login_manual,
    poll_oauth_login,
    save_api_key,
    start_oauth_login,
)
from paperless_agent.inbox_worker import cancel_active_file, is_processing
from paperless_agent.job_control import get_active_file_id
from paperless_agent.llm import resolve_model_name
from paperless_agent.ollama_setup import (
    apply_llm_provider,
    enable_ollama,
    list_running_models,
    ollama_status,
    pull_model,
    restart_ollama,
    start_ollama,
    unload_model,
)
from paperless_agent.privacy import (
    accept_cloud_disclaimer,
    cloud_disclaimer_status,
    revoke_cloud_disclaimer,
)
from paperless_agent.usage import usage_snapshot

router = APIRouter(tags=["auth"])


@router.get("/api/health")
def health() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "ok",
        "llm_provider": config.LLM_PROVIDER,
        "model": resolve_model_name(),
        "configured_model": config.MODEL_NAME,
        "embedding_model": config.EMBEDDING_MODEL,
        "auth": codex_auth_status(),
        "cloud_disclaimer": cloud_disclaimer_status(),
        "usage": usage_snapshot(),
    }
    if config.LLM_PROVIDER == "ollama":
        ollama = ollama_status()
        payload["ollama"] = ollama
        if not ollama.get("ready"):
            payload["status"] = "degraded"
            payload["llm_error"] = ollama.get("error") or (
                f"Missing Ollama models: {', '.join(ollama.get('missing_models') or [])}"
                if ollama.get("missing_models")
                else "Local Ollama is not ready"
            )
    return payload


@router.get("/api/auth/status")
def api_auth_status() -> dict[str, Any]:
    return {
        "status": "success",
        **codex_auth_status(),
        "cloud_disclaimer": cloud_disclaimer_status(),
    }


@router.get("/api/privacy/cloud-disclaimer")
def api_cloud_disclaimer_status() -> dict[str, Any]:
    return {"status": "success", "cloud_disclaimer": cloud_disclaimer_status()}


@router.post("/api/privacy/cloud-disclaimer")
def api_cloud_disclaimer_set(body: CloudDisclaimerRequest) -> dict[str, Any]:
    status = accept_cloud_disclaimer() if body.accepted else revoke_cloud_disclaimer()
    return {"status": "success", "cloud_disclaimer": status}


@router.post("/api/auth/openai/start")
def api_auth_openai_start() -> dict[str, Any]:
    require_cloud_disclaimer_or_403()
    try:
        return start_oauth_login()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/auth/openai/poll")
def api_auth_openai_poll(state: str = Query(..., min_length=8)) -> dict[str, Any]:
    return poll_oauth_login(state)


@router.post("/api/auth/openai/complete")
def api_auth_openai_complete(body: OAuthCompleteRequest) -> dict[str, Any]:
    require_cloud_disclaimer_or_403()
    try:
        return complete_oauth_login_manual(state=body.state, raw=body.callback)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/auth/api-key")
def api_auth_api_key(body: ApiKeyRequest) -> dict[str, Any]:
    require_cloud_disclaimer_or_403()
    try:
        return save_api_key(body.api_key)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/auth/logout")
def api_auth_logout() -> dict[str, Any]:
    return clear_auth()


@router.get("/api/ollama/status")
def api_ollama_status() -> dict[str, Any]:
    return {"status": "success", "ollama": ollama_status()}


@router.post("/api/ollama/enable")
def api_ollama_enable(body: OllamaEnableRequest) -> dict[str, Any]:
    try:
        result = enable_ollama(
            base_url=body.base_url,
            chat_model=body.chat_model,
            embedding_model=body.embedding_model,
            persist=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    pulled: list[dict[str, Any]] = []
    if body.pull_missing:
        for name in list(result.get("ollama", {}).get("missing_models") or []):
            try:
                pulled.append(pull_model(name, base_url=body.base_url))
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=str(exc)) from exc
        result["ollama"] = ollama_status(base_url=body.base_url)
        result["pulled"] = pulled
    return result


@router.post("/api/ollama/start")
def api_ollama_start() -> dict[str, Any]:
    try:
        return start_ollama()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/api/ollama/pull")
def api_ollama_pull(body: OllamaPullRequest) -> dict[str, Any]:
    try:
        return pull_model(body.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/api/llm/provider")
def api_llm_provider(body: LlmProviderRequest) -> dict[str, Any]:
    provider = (body.provider or "").strip().lower()
    if provider in {"openai", "codex", "gemini", "google"}:
        require_cloud_disclaimer_or_403()
    try:
        applied = apply_llm_provider(
            body.provider,
            model=body.model,
            embedding_model=body.embedding_model,
            base_url=body.base_url,
            persist=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload: dict[str, Any] = {"status": "success", "applied": applied}
    if applied["provider"] == "ollama":
        payload["ollama"] = ollama_status(base_url=body.base_url)
    return payload


@router.get("/api/ollama/ps")
def api_ollama_ps() -> dict[str, Any]:
    try:
        return list_running_models()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/ollama/unload")
def api_ollama_unload() -> dict[str, Any]:
    try:
        return unload_model()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/ollama/restart")
async def api_ollama_restart(body: OllamaRestartRequest) -> dict[str, Any]:
    if is_processing() and not body.force:
        raise HTTPException(
            status_code=409,
            detail="documents are being processed — cancel the active file or use force=true",
        )
    if is_processing() and body.force:
        file_id = get_active_file_id()
        if file_id:
            await cancel_active_file(file_id)
        for _ in range(30):
            if not is_processing():
                break
            await asyncio.sleep(0.1)
        if is_processing():
            raise HTTPException(
                status_code=409,
                detail="could not stop processing — try again in a moment",
            )
    try:
        return restart_ollama()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
