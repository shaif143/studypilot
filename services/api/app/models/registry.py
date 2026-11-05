"""Provider selection and the cloud-consent gate."""

from __future__ import annotations

import logging

from app.config import Settings
from app.models.base import (
    ConsentRequiredError,
    EmbeddingProvider,
    LLMProvider,
    ProviderInfo,
    ProviderUnavailableError,
)
from app.models.embeddings import cached_embedder, probe_embedding_providers
from app.models.llm import cached_llm, probe_llm_providers

log = logging.getLogger(__name__)


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Return the configured embedder, falling back to the offline default."""
    try:
        return cached_embedder(settings.embedding_provider, settings.embedding_model)
    except ProviderUnavailableError as exc:
        log.warning(
            "embedding_provider_unavailable",
            extra={"provider": settings.embedding_provider, "reason": str(exc)},
        )
        return cached_embedder("hash", "")


def get_llm_provider(
    settings: Settings,
    *,
    cloud_consent: bool,
    requested: str | None = None,
    model: str | None = None,
) -> LLMProvider:
    """Return a generation provider, enforcing the cloud-consent gate.

    A provider that transmits content off the device is refused unless the
    installation permits cloud use (`Settings.cloud_processing_permitted`)
    and the caller carries the user's consent — the stored preference plus
    the per-request flag, combined into ``cloud_consent`` upstream.
    """
    key = requested or settings.llm_provider
    try:
        # The user's choice of model for this provider wins over the
        # installation's; both fall through to the provider's own default.
        provider = cached_llm(key, model or settings.llm_model)
    except ProviderUnavailableError as exc:
        log.warning("llm_provider_unknown", extra={"provider": key, "reason": str(exc)})
        return cached_llm("extractive", "")

    info = provider.info
    if info.sends_data_off_device and not (cloud_consent and settings.cloud_processing_permitted):
        raise ConsentRequiredError(
            f"{info.label} would send your course material off this device. "
            "Enable cloud processing in Settings to use it."
        )
    if not info.available:
        raise ProviderUnavailableError(info.detail or f"{info.label} is not available.")
    return provider


def available_llm_providers(settings: Settings) -> list[ProviderInfo]:
    return probe_llm_providers(settings.llm_model)


def available_embedding_providers(settings: Settings) -> list[ProviderInfo]:
    return probe_embedding_providers(settings.embedding_provider, settings.embedding_model)
