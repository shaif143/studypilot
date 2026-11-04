"""Provider-neutral interfaces for embeddings and text generation.

Nothing above this layer knows whether inference happens on-device or in a
cloud API.  Each provider declares, in :class:`ProviderInfo`, whether it sends
content off the device — the privacy UI and the consent gate read that flag
rather than hard-coding provider names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ProviderInfo:
    """Describes a provider so the UI can explain it truthfully."""

    key: str
    label: str
    #: True when text is transmitted to a third party.
    sends_data_off_device: bool
    #: True when the provider is actually usable right now.
    available: bool
    #: Why it is unavailable, or a note about quality limits.
    detail: str = ""
    #: True when the provider performs real language generation. The offline
    #: extractive provider sets this to False so the UI never implies otherwise.
    generative: bool = True
    requires_api_key: bool = False
    model: str = ""


@dataclass
class EvidenceSnippet:
    """One retrieved passage made available to a provider.

    Carried structurally (not only inside the rendered prompt) so that the
    offline extractive provider can select and quote from it directly, and so
    citations can be resolved without parsing model output.
    """

    marker: int
    text: str
    page_number: int
    document_title: str = ""
    section: str = ""


@dataclass
class GenerationRequest:
    system: str
    #: Fully rendered prompt for providers that perform real generation.
    user: str
    #: The student's raw question, used by the extractive provider for ranking.
    question: str = ""
    #: Retrieved evidence with [n] markers already assigned by the caller.
    evidence: list[EvidenceSnippet] = field(default_factory=list)
    max_output_tokens: int = 900
    temperature: float = 0.2
    #: Opaque structured-output hint; providers that cannot honour it ignore it.
    json_schema: dict[str, object] | None = None
    timeout_seconds: float = 90.0


@dataclass
class GenerationResult:
    text: str
    provider: str
    model: str
    #: True when the text was produced by a language model rather than by
    #: deterministic extraction from the source material.
    generative: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    #: Non-fatal notes (truncation, retries, degraded mode) shown to the user.
    warnings: list[str] = field(default_factory=list)


class ProviderUnavailableError(RuntimeError):
    """Raised when a provider cannot serve a request (no key, no network)."""


class ConsentRequiredError(PermissionError):
    """Raised when a request would send content off-device without consent."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def info(self) -> ProviderInfo: ...

    @property
    def dimensions(self) -> int: ...

    @property
    def model_id(self) -> str:
        """Stable identifier recorded against every indexed chunk."""
        ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


@runtime_checkable
class LLMProvider(Protocol):
    @property
    def info(self) -> ProviderInfo: ...

    def generate(self, request: GenerationRequest) -> GenerationResult: ...
