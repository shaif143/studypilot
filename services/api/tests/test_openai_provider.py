"""ChatGPT's models through the OpenAI API.

OpenAI is a fake here. What is tested is what the provider decides: which of
an account's models it uses when none is chosen, that a reasoning model's
refusal of a temperature is retried without one, and that an empty answer is
an error rather than an answer.
"""

from __future__ import annotations

import httpx
import pytest

from app.models import llm
from app.models.base import GenerationRequest, ProviderUnavailableError


@pytest.fixture(autouse=True)
def _fresh_cache():
    llm._openai_models_cache.clear()
    yield
    llm._openai_models_cache.clear()


def _response(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body, request=httpx.Request("POST", "https://x/"))


def test_the_newest_chat_model_is_chosen_from_what_the_account_lists():
    ids = [
        "gpt-4.1", "gpt-5.1", "gpt-5.5-mini", "gpt-5.5", "gpt-5.5-2026-03-01", "gpt-5-nano",
        "gpt-audio", "gpt-image-2", "gpt-5-codex", "text-embedding-3-large", "o3", "whisper-1",
        "gpt-5.6-preview",
    ]  # fmt: skip
    ranked = llm.rank_openai_models(ids)
    # Newest first, the plain model before its small variants; no snapshots,
    # previews, audio, image, code or embedding models.
    assert ranked == ["gpt-5.5", "gpt-5.5-mini", "gpt-5.1", "gpt-5-nano", "gpt-4.1"]
    assert llm.rank_openai_models(["whisper-1", "dall-e-3"]) == []


def test_models_are_listed_once_and_no_key_means_no_request(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _response(200, {"data": [{"id": "gpt-5.5"}, {"id": "gpt-5.5-mini"}]})

    monkeypatch.setattr(llm.httpx, "get", fake_get)
    assert llm.OpenAIProvider(api_key="").info.available is False
    assert "Add an OpenAI API key" in llm.OpenAIProvider(api_key="").info.detail
    assert calls == []

    provider = llm.OpenAIProvider(api_key="k")
    info = provider.info
    assert info.available and info.model == "gpt-5.5" and info.label.startswith("ChatGPT")
    assert [m["id"] for m in info.models] == ["gpt-5.5", "gpt-5.5-mini"]
    assert llm.OpenAIProvider(api_key="k").model == "gpt-5.5"
    assert len(calls) == 1  # cached, not asked for on every look
    # A model the student picked is used as it is.
    assert llm.OpenAIProvider("gpt-5.5-mini", api_key="k").model == "gpt-5.5-mini"


def test_a_key_that_lists_nothing_is_said_to_be_unusable(monkeypatch):
    monkeypatch.setattr(llm.httpx, "get", lambda url, **kw: _response(401, {"error": {}}))
    info = llm.OpenAIProvider(api_key="bad").info
    assert info.available is False and "not accepted" in info.detail
    with pytest.raises(ProviderUnavailableError):
        llm.OpenAIProvider(api_key="bad").generate(GenerationRequest(system="s", user="u"))


def test_a_refused_temperature_is_retried_without_one(monkeypatch):
    sent: list[dict] = []

    def fake_post(url, json, **kwargs):
        sent.append(dict(json))
        if "temperature" in json:
            return _response(
                400,
                {"error": {"message": "Unsupported value: 'temperature' does not support 0.2"}},
            )
        return _response(
            200,
            {"choices": [{"message": {"content": "An answer."}}], "usage": {"prompt_tokens": 3}},
        )

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    provider = llm.OpenAIProvider("gpt-5.5", api_key="k")
    result = provider.generate(
        GenerationRequest(system="s", user="u", max_output_tokens=1000, temperature=0.2)
    )
    assert result.text == "An answer." and result.provider == "openai"
    assert len(sent) == 2 and "temperature" in sent[0] and "temperature" not in sent[1]
    # Room is left for a reasoning model to think before it writes.
    assert sent[0]["max_completion_tokens"] == 4000


def test_an_error_and_an_empty_answer_are_failures_not_answers(monkeypatch):
    provider = llm.OpenAIProvider("gpt-5.5", api_key="k")
    monkeypatch.setattr(
        llm.httpx, "post", lambda url, **kw: _response(429, {"error": {"message": "Rate limit"}})
    )
    with pytest.raises(ProviderUnavailableError, match="429"):
        provider.generate(GenerationRequest(system="s", user="u"))

    monkeypatch.setattr(
        llm.httpx, "post", lambda url, **kw: _response(200, {"choices": [{"message": {}}]})
    )
    with pytest.raises(ProviderUnavailableError, match="empty"):
        provider.generate(GenerationRequest(system="s", user="u"))


def test_the_local_model_is_given_a_window_that_fits_the_prompt(monkeypatch):
    sent: list[dict] = []

    def fake_post(url, json, **kwargs):
        sent.append(json)
        return _response(200, {"message": {"content": "ok"}})

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    provider = llm.OllamaProvider("llama3.1:8b")
    provider.generate(GenerationRequest(system="s" * 30, user="u" * 30, max_output_tokens=500))
    assert sent[-1]["options"]["num_ctx"] == 4096  # never smaller than this
    provider.generate(
        GenerationRequest(system="s" * 9000, user="u" * 21000, max_output_tokens=2000)
    )
    assert sent[-1]["options"]["num_ctx"] == 12000
    provider.generate(GenerationRequest(system="s", user="u" * 200_000, max_output_tokens=2000))
    assert sent[-1]["options"]["num_ctx"] == 16384  # and never larger than this
