"""User settings, privacy preferences and model-provider discovery."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app import secrets
from app.api.deps import ContextDep, UserDep, UsersDep, require_token
from app.api.schemas import (
    ProviderInfoOut,
    ProviderKeyIn,
    ProviderKeysOut,
    ProviderKeyStatusOut,
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
def providers(context: ContextDep, user: UserDep, users: UsersDep) -> ProvidersResponse:
    """Report what this installation can actually run, and what leaves the device."""
    settings = context.settings
    llm = [ProviderInfoOut(**info.__dict__) for info in available_llm_providers(settings)]
    embedding = [
        ProviderInfoOut(**info.__dict__) for info in available_embedding_providers(settings)
    ]
    # The user's own choice wins over the installation default.
    chosen = users.settings(user.id).preferred_llm_provider or settings.llm_provider
    active = next((info for info in llm if info.key == chosen), None)
    return ProvidersResponse(
        llm=llm,
        embedding=embedding,
        active_llm=chosen,
        active_embedding=context.embedder.model_id,
        active_is_generative=bool(active and active.generative and active.available),
    )


# --- provider keys, desktop only ------------------------------------------
def _keys_response(context: ContextDep) -> ProviderKeysOut:
    settings = context.settings
    return ProviderKeysOut(
        editable=not settings.is_hosted,
        keys=[
            ProviderKeyStatusOut(provider=name, **flags)
            for name, flags in secrets.status(settings).items()
        ],
    )


@router.get("/keys", response_model=ProviderKeysOut)
def provider_keys(context: ContextDep) -> ProviderKeysOut:
    """Which cloud providers have a key. Never the key itself."""
    return _keys_response(context)


def _require_desktop(context: ContextDep) -> None:
    if context.settings.is_hosted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Keys on a hosted deployment belong to its operator and are set in the "
                "service's environment, not here."
            ),
        )


@router.post("/keys/{provider}", response_model=ProviderKeysOut)
def set_provider_key(provider: str, body: ProviderKeyIn, context: ContextDep) -> ProviderKeysOut:
    """Store a key on this Mac, in the application's data directory (mode 0600)."""
    _require_desktop(context)
    if provider not in secrets.ENV_FOR_PROVIDER:
        raise HTTPException(status_code=404, detail=f"No such cloud provider: {provider}")
    try:
        secrets.set_key(context.settings, provider, body.key)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _keys_response(context)


@router.delete("/keys/{provider}", response_model=ProviderKeysOut)
def delete_provider_key(provider: str, context: ContextDep) -> ProviderKeysOut:
    _require_desktop(context)
    if provider not in secrets.ENV_FOR_PROVIDER:
        raise HTTPException(status_code=404, detail=f"No such cloud provider: {provider}")
    secrets.delete_key(context.settings, provider)
    return _keys_response(context)
