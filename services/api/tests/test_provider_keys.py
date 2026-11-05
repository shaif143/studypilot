"""Keys typed into the desktop app, and the user's choice of provider.

The desktop app has no environment to configure, so the key the user enters
is kept in its data directory. These pin the properties that make that safe:
the file is private to the account, values never come back out of the API,
the environment wins when it already holds a key, and a hosted deployment
refuses the whole feature.
"""

from __future__ import annotations

import os
import stat

import pytest
from fastapi.testclient import TestClient

from app import secrets
from app.config import Settings


@pytest.fixture(autouse=True)
def _no_ambient_keys(monkeypatch):
    for variable in secrets.ENV_FOR_PROVIDER.values():
        monkeypatch.delenv(variable, raising=False)


def test_a_stored_key_is_private_and_loads_into_the_environment(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    secrets.set_key(settings, "anthropic", "sk-ant-test-value-0000")

    path = tmp_path / secrets.FILENAME
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-test-value-0000"

    # A fresh process: nothing in the environment, the file is the source.
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert secrets.load_into_environment(settings) == ["anthropic"]
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-test-value-0000"


def test_the_environment_wins_and_is_reported_as_not_removable(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "from-the-shell")
    secrets.set_key(settings, "anthropic", "sk-ant-file")

    # A fresh process: the shell set Gemini, the file holds Anthropic.
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert secrets.load_into_environment(settings) == ["anthropic"]
    assert os.environ["GEMINI_API_KEY"] == "from-the-shell"
    report = secrets.status(settings)
    assert report["gemini"] == {"configured": True, "from_environment": True}
    assert report["anthropic"] == {"configured": True, "from_environment": False}


def test_deleting_a_key_removes_it_everywhere(tmp_path):
    settings = Settings(data_dir=tmp_path)
    secrets.set_key(settings, "gemini", "gm-test-value-00000000")
    secrets.delete_key(settings, "gemini")
    assert "GEMINI_API_KEY" not in os.environ
    assert not (tmp_path / secrets.FILENAME).exists()
    assert secrets.status(settings)["gemini"]["configured"] is False


def test_a_key_with_whitespace_is_refused(tmp_path):
    with pytest.raises(ValueError, match="whitespace"):
        secrets.set_key(Settings(data_dir=tmp_path), "anthropic", "sk-ant pasted badly")


def test_hosted_mode_never_reads_the_file(tmp_path):
    hosted = Settings(data_dir=tmp_path, deployment_mode="hosted", jwt_secret="x" * 40)
    (tmp_path / secrets.FILENAME).write_text('{"anthropic": "sk-ant-should-be-ignored"}')
    assert secrets.load_into_environment(hosted) == []
    assert "ANTHROPIC_API_KEY" not in os.environ


# --- through the API --------------------------------------------------------
def test_the_api_reports_status_but_never_the_value(client: TestClient):
    response = client.post("/api/keys/anthropic", json={"key": "sk-ant-api-test-000000"})
    assert response.status_code == 200
    body = response.json()
    assert body["editable"] is True
    anthropic = next(k for k in body["keys"] if k["provider"] == "anthropic")
    assert anthropic["configured"] is True
    assert "sk-ant" not in response.text

    listing = client.get("/api/keys").json()
    assert "sk-ant" not in str(listing)

    # The provider now reports itself available, so the UI can offer it.
    providers = client.get("/api/providers").json()
    assert next(p for p in providers["llm"] if p["key"] == "anthropic")["available"] is True

    client.delete("/api/keys/anthropic")
    providers = client.get("/api/providers").json()
    assert next(p for p in providers["llm"] if p["key"] == "anthropic")["available"] is False


def test_an_unknown_provider_is_404(client: TestClient):
    assert client.post("/api/keys/openai", json={"key": "sk-whatever-000"}).status_code == 404


def test_the_user_can_choose_the_active_provider(client: TestClient):
    before = client.get("/api/providers").json()["active_llm"]
    assert before == "extractive"

    updated = client.patch("/api/settings", json={"preferred_llm_provider": "ollama"})
    assert updated.status_code == 200
    assert updated.json()["preferred_llm_provider"] == "ollama"
    assert client.get("/api/providers").json()["active_llm"] == "ollama"

    # Clearing it returns to the installation default.
    client.patch("/api/settings", json={"preferred_llm_provider": ""})
    assert client.get("/api/providers").json()["active_llm"] == "extractive"


def test_hosted_api_refuses_key_entry(tmp_path):
    """On a hosted deployment the keys are the operator's; the routes say so."""
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.api import routes_settings

    hosted = Settings(data_dir=tmp_path, deployment_mode="hosted", jwt_secret="x" * 40)
    context = SimpleNamespace(settings=hosted)
    assert routes_settings._keys_response(context).editable is False
    with pytest.raises(HTTPException) as refused:
        routes_settings._require_desktop(context)
    assert refused.value.status_code == 404
    assert "operator" in str(refused.value.detail)
