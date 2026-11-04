"""User settings, privacy preferences and model-provider discovery."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import ContextDep, UserDep, UsersDep, require_token
from app.api.schemas import (
    ProviderInfoOut,
    ProvidersResponse,
    SettingsOut,
    SettingsUpdate,
)
from app.models.registry import available_embedding_providers, available_llm_providers

router = APIRouter(prefix="/api", tags=["settings"], dependencies=[Depends(require_token)])


@router.get("/settings", response_model=SettingsOut)
def read_settings(user: UserDep, users: UsersDep) -> SettingsOut:
    return SettingsOut.model_validate(users.settings(user.id))


@router.patch("/settings", response_model=SettingsOut)
def update_settings(payload: SettingsUpdate, user: UserDep, users: UsersDep) -> SettingsOut:
    values = payload.model_dump(exclude_unset=True)
    return SettingsOut.model_validate(users.update_settings(user.id, values))


@router.get("/providers", response_model=ProvidersResponse)
def providers(context: ContextDep) -> ProvidersResponse:
    """Report what this installation can actually run, and what leaves the device."""
    settings = context.settings
    llm = [ProviderInfoOut(**info.__dict__) for info in available_llm_providers(settings)]
    embedding = [
        ProviderInfoOut(**info.__dict__) for info in available_embedding_providers(settings)
    ]
    active = next((info for info in llm if info.key == settings.llm_provider), None)
    return ProvidersResponse(
        llm=llm,
        embedding=embedding,
        active_llm=settings.llm_provider,
        active_embedding=context.embedder.model_id,
        active_is_generative=bool(active and active.generative and active.available),
    )
