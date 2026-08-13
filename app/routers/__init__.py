"""API router package."""

from __future__ import annotations

from fastapi import APIRouter

from app.routers import auth, documents, processing, reviews, settings, updates


def build_api_router() -> APIRouter:
    """Compose feature routers into a single includable router."""
    api = APIRouter()
    api.include_router(auth.router)
    api.include_router(settings.router)
    api.include_router(processing.router)
    api.include_router(updates.router)
    api.include_router(reviews.router)
    api.include_router(documents.router)
    return api
