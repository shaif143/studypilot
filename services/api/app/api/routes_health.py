"""Liveness and runtime information."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app import __version__
from app.api.deps import ContextDep, require_token
from app.api.schemas import HealthResponse

#: Unauthenticated: used by the desktop shell to detect that the sidecar is up.
public_router = APIRouter(tags=["health"])
router = APIRouter(tags=["health"], dependencies=[Depends(require_token)])


@public_router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness only. Deliberately exposes nothing about the user's data."""
    return {"status": "ok", "version": __version__}


@router.get("/api/health", response_model=HealthResponse)
def health(context: ContextDep) -> HealthResponse:
    return HealthResponse(status="ok", version=__version__, runtime=context.health())
