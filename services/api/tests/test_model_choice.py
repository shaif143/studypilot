"""Choosing the model a provider answers with.

The user picks per provider in Settings; the choice reaches the provider
that answers, never leaks to another provider, and a model id has to look
like one. The Anthropic default is Opus 5.5, with Fable, Sonnet and Haiku
offered.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agents.service import resolve_provider
from app.config import Settings
from app.models.llm import MODEL_CHOICES, AnthropicProvider
from app.storage.models import UserSettings


def test_anthropic_defaults_to_opus_and_offers_the_claude_5_family():
    assert AnthropicProvider().model == "claude-opus-5-5"
    ids = [choice["id"] for choice in MODEL_CHOICES["anthropic"]]
    assert ids[0] == "claude-opus-5-5"
    assert {"claude-fable-5-2", "claude-sonnet-5-5", "claude-haiku-5-5"} <= set(ids)
    assert AnthropicProvider().info.models == MODEL_CHOICES["anthropic"]


def test_the_users_choice_reaches_the_provider_that_answers(settings: Settings, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-000000")
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test-000000")
    # The consent gate is not the subject here: the installation permits cloud use.
    settings = settings.model_copy(update={"allow_cloud_processing": True})
    user = UserSettings(
        user_id="u",
        allow_cloud_processing=True,
        preferred_llm_provider="anthropic",
        preferred_llm_models={"anthropic": "claude-fable-5-2", "gemini": "gemini-3.5-flash-lite"},
    )
    provider, warnings = resolve_provider(settings, user, requested=None, allow_cloud_request=True)
    assert provider.model == "claude-fable-5-2" and warnings == []

    # A request that names the other provider gets *that* provider's choice.
    provider, _ = resolve_provider(settings, user, requested="gemini", allow_cloud_request=True)
    assert provider.model == "gemini-3.5-flash-lite"

    # No choice for a provider: its own default, not another provider's id.
    user.preferred_llm_models = {"gemini": "gemini-3.5-flash-lite"}
    provider, _ = resolve_provider(settings, user, requested="anthropic", allow_cloud_request=True)
    assert provider.model == "claude-opus-5-5"


def test_an_older_database_row_without_the_column_reads_as_no_choice(settings: Settings):
    user = UserSettings(user_id="u", preferred_llm_provider="anthropic", preferred_llm_models=None)
    provider, _ = resolve_provider(
        settings, user, requested="extractive", allow_cloud_request=False
    )
    assert provider.info.key == "extractive"


# --- through the API ------------------------------------------------------------
def test_settings_store_the_choice_and_providers_report_it(client: TestClient, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-000000")
    before = client.get("/api/providers").json()
    anthropic = next(p for p in before["llm"] if p["key"] == "anthropic")
    assert anthropic["models"][0]["id"] == "claude-opus-5-5"
    assert anthropic["model"] == "claude-opus-5-5"

    updated = client.patch(
        "/api/settings",
        json={
            "preferred_llm_provider": "anthropic",
            "preferred_llm_models": {"anthropic": "claude-fable-5-2"},
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["preferred_llm_models"] == {"anthropic": "claude-fable-5-2"}

    providers = client.get("/api/providers").json()
    assert providers["active_llm"] == "anthropic"
    assert providers["active_model"] == "claude-fable-5-2"

    # An empty id clears the choice; the default is what is reported again.
    client.patch("/api/settings", json={"preferred_llm_models": {"anthropic": ""}})
    assert client.get("/api/settings").json()["preferred_llm_models"] == {}
    assert client.get("/api/providers").json()["active_model"] == "claude-opus-5-5"


@pytest.mark.parametrize(
    "payload",
    [
        {"openai": "gpt-5"},
        {"anthropic": "claude opus"},
        {"anthropic": "-leading-dash"},
        {"anthropic": "x" * 97},
    ],
)
def test_a_model_id_has_to_look_like_one(client: TestClient, payload):
    response = client.patch("/api/settings", json={"preferred_llm_models": payload})
    assert response.status_code == 422


# --- replies that carry no answer ---------------------------------------------
class _Reply:
    def __init__(self, payload: dict) -> None:
        self.status_code = 200
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _ask(model: str = "claude-fable-5-2"):
    from app.models.base import GenerationRequest

    provider = AnthropicProvider(model, api_key="sk-ant-test-000000")
    return provider.generate(GenerationRequest(system="s", user="u", max_output_tokens=900))


def test_a_reply_that_spent_its_limit_thinking_is_asked_for_again_with_room(monkeypatch):
    """Thinking counts towards the limit, so a small one can end before the answer."""
    from app.models import llm

    sent: list[int] = []

    def fake_post(url, json, **kwargs):
        sent.append(json["max_tokens"])
        if len(sent) == 1:
            return _Reply(
                {"content": [{"type": "thinking", "thinking": ""}], "stop_reason": "max_tokens"}
            )
        return _Reply(
            {"content": [{"type": "text", "text": "The answer."}], "stop_reason": "end_turn"}
        )

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    assert _ask().text == "The answer."
    assert sent == [900, 7800]


def test_a_declined_or_empty_reply_is_an_error_not_an_empty_answer(monkeypatch):
    from app.models import llm
    from app.models.base import ProviderUnavailableError

    monkeypatch.setattr(
        llm.httpx, "post", lambda url, **kw: _Reply({"content": [], "stop_reason": "refusal"})
    )
    with pytest.raises(ProviderUnavailableError, match="declined this request"):
        _ask()

    monkeypatch.setattr(
        llm.httpx, "post", lambda url, **kw: _Reply({"content": [], "stop_reason": "max_tokens"})
    )
    with pytest.raises(ProviderUnavailableError, match="returned no text"):
        _ask()
