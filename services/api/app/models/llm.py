"""Text-generation providers.

Four implementations:

``extractive``
    The default.  Performs **no** language generation: it ranks sentences from
    the retrieved course material and quotes them verbatim with citations.  Its
    :attr:`ProviderInfo.generative` flag is ``False`` so every surface can say
    plainly that this is source quoting, not tutoring.  It exists so the whole
    pipeline — ingestion, retrieval, citation, assessment, progress — is real
    and testable with no API key and no network.
``ollama``
    A local inference runtime.  On-device, no API key, optional install.
``anthropic`` / ``gemini``
    Hosted APIs.  Both set ``sends_data_off_device=True``; the service layer
    refuses to call them unless the user has recorded explicit consent.
    Both read images; Gemini also reads audio, which is how image and audio
    assignment briefs are transcribed on an installation without a local
    speech model.

Keys are read from the environment or the macOS keychain, never from the
frontend bundle.
"""

from __future__ import annotations

import base64
import json
import os
import re
from functools import lru_cache

import httpx

from app.models.base import (
    GenerationRequest,
    GenerationResult,
    LLMProvider,
    ProviderInfo,
    ProviderUnavailableError,
)

_SENTENCE_RE = re.compile(r"(?<=[.!?;:])\s+|\n+")
_WORD_RE = re.compile(r"[A-Za-z0-9']+")

_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "do",
        "does",
        "for",
        "from",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "so",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "explain",
        "describe",
        "define",
        "give",
        "tell",
        "me",
        "about",
    ]
)


def _content_words(text: str) -> set[str]:
    return {
        w
        for w in (m.group(0).lower() for m in _WORD_RE.finditer(text))
        if w not in _STOPWORDS and len(w) > 2
    }


class ExtractiveProvider:
    """Deterministic, offline, non-generative answer composer.

    Selects the sentences from retrieved course material that best overlap the
    question and returns them as a quoted, cited passage.  It never writes new
    factual claims, so it cannot hallucinate; its weakness is that it cannot
    paraphrase, synthesise or teach.
    """

    MAX_SENTENCES = 6

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            key="extractive",
            label="Source quoting (no AI model)",
            sends_data_off_device=False,
            available=True,
            generative=False,
            detail=(
                "Works with no model installed and no network. It quotes the most "
                "relevant lines from your own material instead of explaining them. "
                "Connect a local or cloud model for real explanations."
            ),
            model="extractive-v1",
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if not request.evidence:
            return GenerationResult(
                text=(
                    "I could not find anything in this course's material that answers that. "
                    "Nothing here is generated, so I will not guess — try rephrasing, or "
                    "upload the lecture that covers it."
                ),
                provider="extractive",
                model="extractive-v1",
                generative=False,
                warnings=["no_evidence"],
            )

        question_words = _content_words(request.question or request.user)
        scored: list[tuple[float, int, int, str]] = []
        for snippet in request.evidence:
            for order, raw in enumerate(_SENTENCE_RE.split(snippet.text)):
                sentence = " ".join(raw.split())
                if len(sentence) < 25:
                    continue
                words = _content_words(sentence)
                if not words:
                    continue
                overlap = len(question_words & words)
                # Favour overlap with the question, lightly penalise very long
                # sentences, and keep original order as the tie-break.
                score = overlap / (1.0 + 0.004 * len(sentence))
                if overlap == 0:
                    score = 0.0
                scored.append((score, -order, snippet.marker, sentence))

        scored.sort(reverse=True)
        chosen = [item for item in scored if item[0] > 0][: self.MAX_SENTENCES]
        if not chosen:
            # Nothing matched the question's wording; fall back to the opening of
            # the single best-ranked passage rather than inventing a link.
            best = request.evidence[0]
            lead = " ".join(best.text.split())[:600]
            return GenerationResult(
                text=(
                    "No line in your material directly answers that wording. The closest "
                    f"passage is on page {best.page_number}:\n\n"
                    f"> {lead} [{best.marker}]\n\n"
                    "_Quoted from your material, not explained — no AI model is configured._"
                ),
                provider="extractive",
                model="extractive-v1",
                generative=False,
                warnings=["weak_match"],
            )

        # Restore document order for readability.
        chosen.sort(key=lambda item: (item[2], -item[1]))
        lines = [f"> {sentence} [{marker}]" for _, _, marker, sentence in chosen]
        body = "\n>\n".join(lines)
        text = (
            "Here are the passages from your own material that cover this:\n\n"
            f"{body}\n\n"
            "_These lines are quoted directly from your documents. No AI model is "
            "configured, so nothing above has been explained or rewritten._"
        )
        return GenerationResult(
            text=text,
            provider="extractive",
            model="extractive-v1",
            generative=False,
        )


class OllamaProvider:
    """Local inference through an Ollama runtime on this machine."""

    def __init__(self, model: str = "", base_url: str = "") -> None:
        self.model = model or os.environ.get("STUDYPILOT_OLLAMA_MODEL", "llama3.1:8b")
        self.base_url = (
            base_url or os.environ.get("STUDYPILOT_OLLAMA_URL", "http://127.0.0.1:11434")
        ).rstrip("/")

    @property
    def info(self) -> ProviderInfo:
        reachable, detail = self.probe()
        return ProviderInfo(
            key="ollama",
            label=f"Ollama (local, {self.model})",
            sends_data_off_device=False,
            available=reachable,
            generative=True,
            detail=detail,
            model=self.model,
        )

    def probe(self) -> tuple[bool, str]:
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=1.5)
            response.raise_for_status()
            names = {m.get("name", "") for m in response.json().get("models", [])}
        except Exception:
            return False, (
                f"No Ollama runtime reachable at {self.base_url}. "
                "Install Ollama and run it to use fully on-device generation."
            )
        if self.model not in names:
            return (
                False,
                f"Ollama is running but '{self.model}' is not pulled. "
                f"Run: ollama pull {self.model}",
            )
        return True, "Runs entirely on this Mac. Nothing leaves the device."

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if request.attachments:
            raise ProviderUnavailableError(
                f"{self.model} is a text model and cannot read "
                f"{request.attachments[0].media_type}. Use a cloud provider for image or "
                "audio briefs, or convert the file to a PDF."
            )
        payload = {
            "model": self.model,
            "stream": False,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_output_tokens,
            },
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
        }
        if request.json_schema:
            payload["format"] = "json"
        try:
            response = httpx.post(
                f"{self.base_url}/api/chat", json=payload, timeout=request.timeout_seconds
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise ProviderUnavailableError(f"Ollama request failed: {exc}") from exc
        return GenerationResult(
            text=data.get("message", {}).get("content", ""),
            provider="ollama",
            model=self.model,
            generative=True,
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
        )


class AnthropicProvider:
    """Hosted Claude models. Sends content off-device."""

    DEFAULT_MODEL = "claude-sonnet-5-5"

    def __init__(self, model: str = "", api_key: str | None = None) -> None:
        self.model = model or self.DEFAULT_MODEL
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            key="anthropic",
            label=f"Anthropic API ({self.model})",
            sends_data_off_device=True,
            available=bool(self.api_key),
            generative=True,
            requires_api_key=True,
            detail=(
                "Strongest explanations; reads image briefs. Lecture text and your "
                "questions are sent to Anthropic and billed per token."
                if self.api_key
                else "Add ANTHROPIC_API_KEY to the backend environment to enable this provider."
            ),
            model=self.model,
            accepts_images=True,
            accepts_audio=False,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if not self.api_key:
            raise ProviderUnavailableError("No Anthropic API key configured.")
        content: list[dict[str, object]] = []
        for attachment in request.attachments:
            if not attachment.media_type.startswith("image/"):
                raise ProviderUnavailableError(
                    f"Anthropic cannot read {attachment.media_type}; audio needs Gemini or a "
                    "local Whisper model."
                )
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": attachment.media_type,
                        "data": base64.b64encode(attachment.data).decode("ascii"),
                    },
                }
            )
        content.append({"type": "text", "text": request.user})
        body: dict[str, object] = {
            "model": self.model,
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "system": request.system,
            "messages": [{"role": "user", "content": content}],
        }
        try:
            response = httpx.post(
                "https://api.anthropic.com/v1/messages",
                json=body,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                timeout=request.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise ProviderUnavailableError(f"Anthropic request failed: {exc}") from exc
        text = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        usage = data.get("usage", {})
        return GenerationResult(
            text=text,
            provider="anthropic",
            model=self.model,
            generative=True,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )


class OpenAIProvider:
    """Hosted OpenAI models. Sends content off-device."""

    DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(self, model: str = "", api_key: str | None = None) -> None:
        self.model = model or self.DEFAULT_MODEL
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            key="openai",
            label=f"OpenAI API ({self.model})",
            sends_data_off_device=True,
            available=bool(self.api_key),
            generative=True,
            requires_api_key=True,
            detail=(
                "Lecture text and your questions are sent to OpenAI and billed per token."
                if self.api_key
                else "Add an OpenAI API key in Settings to enable this provider."
            ),
            model=self.model,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if not self.api_key:
            raise ProviderUnavailableError("No OpenAI API key configured.")
        body: dict[str, object] = {
            "model": self.model,
            "max_completion_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
        }
        if request.json_schema:
            body["response_format"] = {"type": "json_object"}
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "content-type": "application/json",
                },
                timeout=request.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise ProviderUnavailableError(f"OpenAI request failed: {exc}") from exc
        usage = data.get("usage", {})
        return GenerationResult(
            text=data["choices"][0]["message"].get("content", ""),
            provider="openai",
            model=self.model,
            generative=True,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )


class GeminiProvider:
    """Google's hosted Gemini models. Sends content off-device.

    Reads text, images and audio through one endpoint, which makes it the
    provider that can transcribe a recorded assignment brief on a machine
    without a local speech model. The dedicated transcription model is used
    for audio-only requests; everything else goes to the general model.
    """

    DEFAULT_MODEL = "gemini-3.6-flash"
    TRANSCRIBE_MODEL = "gemini-3.5-transcribe"
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, model: str = "", api_key: str | None = None) -> None:
        self.model = model or self.DEFAULT_MODEL
        self.api_key = api_key or os.environ.get(
            "GEMINI_API_KEY", os.environ.get("GOOGLE_API_KEY", "")
        )

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            key="gemini",
            label=f"Gemini API ({self.model})",
            sends_data_off_device=True,
            available=bool(self.api_key),
            generative=True,
            requires_api_key=True,
            detail=(
                "Fast and inexpensive; reads image and audio briefs. Content is sent to "
                "Google and billed per token."
                if self.api_key
                else "Add GEMINI_API_KEY to the backend environment to enable this provider."
            ),
            model=self.model,
            accepts_images=True,
            accepts_audio=True,
        )

    def _model_for(self, request: GenerationRequest) -> str:
        kinds = {a.media_type.split("/", 1)[0] for a in request.attachments}
        if kinds == {"audio"}:
            return self.TRANSCRIBE_MODEL
        return self.model

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if not self.api_key:
            raise ProviderUnavailableError("No Gemini API key configured.")
        parts: list[dict[str, object]] = []
        for attachment in request.attachments:
            parts.append(
                {
                    "inlineData": {
                        "mimeType": attachment.media_type,
                        "data": base64.b64encode(attachment.data).decode("ascii"),
                    }
                }
            )
        parts.append({"text": request.user})
        generation_config: dict[str, object] = {
            "temperature": request.temperature,
            "maxOutputTokens": request.max_output_tokens,
        }
        if request.json_schema:
            generation_config["responseMimeType"] = "application/json"
        body: dict[str, object] = {
            "systemInstruction": {"parts": [{"text": request.system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": generation_config,
        }
        model = self._model_for(request)
        try:
            response = httpx.post(
                f"{self.BASE_URL}/models/{model}:generateContent",
                json=body,
                headers={"x-goog-api-key": self.api_key, "content-type": "application/json"},
                timeout=request.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise ProviderUnavailableError(f"Gemini request failed: {exc}") from exc
        candidates = data.get("candidates") or []
        if not candidates:
            # A blocked prompt comes back with no candidates and a reason.
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise ProviderUnavailableError(f"Gemini returned no answer ({reason}).")
        text = "".join(
            part.get("text", "")
            for part in (candidates[0].get("content") or {}).get("parts", [])
            if isinstance(part, dict)
        )
        usage = data.get("usageMetadata") or {}
        return GenerationResult(
            text=text,
            provider="gemini",
            model=model,
            generative=True,
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
        )


def build_llm(provider: str, model: str = "") -> LLMProvider:
    if provider == "extractive":
        return ExtractiveProvider()
    if provider == "ollama":
        return OllamaProvider(model)
    if provider == "anthropic":
        return AnthropicProvider(model)
    if provider == "gemini":
        return GeminiProvider(model)
    if provider == "openai":
        # Still constructible for anyone who pinned it, but no longer offered.
        return OpenAIProvider(model)
    raise ProviderUnavailableError(f"Unknown LLM provider: {provider}")


@lru_cache(maxsize=8)
def cached_llm(provider: str, model: str = "") -> LLMProvider:
    return build_llm(provider, model)


def probe_llm_providers(model: str = "") -> list[ProviderInfo]:
    """Report the generation providers this installation can actually use."""
    return [
        ExtractiveProvider().info,
        OllamaProvider(model).info,
        AnthropicProvider().info,
        GeminiProvider().info,
    ]


def extract_json_object(text: str) -> dict[str, object] | list[object] | None:
    """Pull the first JSON object or array out of a model response.

    Models wrap JSON in prose or fences; structured output must be validated
    rather than trusted, so this is deliberately tolerant but never guesses at
    missing fields.
    """
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = cleaned.find(opener)
        end = cleaned.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None
