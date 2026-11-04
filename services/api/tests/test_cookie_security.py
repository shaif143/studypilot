"""Session-cookie attributes.

A browser silently discards a ``Secure`` cookie sent over plain HTTP, so
pinning the flag on makes hosted mode impossible to run locally, while pinning
it off strips the protection from the deployment that needs it. The flag is
therefore derived from the request's scheme, which is what these tests pin.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.auth import SESSION_COOKIE, cookie_settings, request_is_secure
from app.config import Settings
from app.main import create_app


def fake_request(scheme: str = "http", forwarded: str | None = None):
    headers = {"x-forwarded-proto": forwarded} if forwarded else {}
    return SimpleNamespace(headers=headers, url=SimpleNamespace(scheme=scheme))


def test_direct_https_request_is_secure():
    assert request_is_secure(fake_request(scheme="https")) is True
    assert request_is_secure(fake_request(scheme="http")) is False


def test_forwarded_header_wins_behind_a_proxy():
    """TLS terminates at the proxy, so only the header records the real scheme."""
    assert request_is_secure(fake_request(scheme="http", forwarded="https")) is True
    assert request_is_secure(fake_request(scheme="https", forwarded="http")) is False


def test_forwarded_header_takes_the_first_hop():
    """Chained proxies append; the client-facing scheme is the first entry."""
    assert request_is_secure(fake_request(forwarded="https, http")) is True
    assert request_is_secure(fake_request(forwarded="http, https")) is False
    assert request_is_secure(fake_request(forwarded="  HTTPS  ")) is True


def test_cookie_is_always_hardened_apart_from_secure(tmp_path):
    settings = Settings(data_dir=tmp_path, deployment_mode="hosted", jwt_secret="s" * 40)
    over_http = cookie_settings(settings, fake_request(scheme="http"))
    over_https = cookie_settings(settings, fake_request(scheme="https"))

    for attributes in (over_http, over_https):
        assert attributes["key"] == SESSION_COOKIE
        assert attributes["httponly"] is True
        # Lax is what removes the need for separate CSRF tokens, given the
        # frontend is served same-origin with the API.
        assert attributes["samesite"] == "lax"
        assert attributes["path"] == "/"

    assert over_http["secure"] is False
    assert over_https["secure"] is True


def test_omitting_the_request_defaults_to_secure(tmp_path):
    """Fail closed when there is no request to judge from."""
    settings = Settings(data_dir=tmp_path, deployment_mode="hosted", jwt_secret="s" * 40)
    assert cookie_settings(settings)["secure"] is True


@pytest.mark.parametrize(
    "base_url,expect_secure", [("https://testserver", True), ("http://testserver", False)]
)
def test_issued_cookie_matches_the_transport(
    hosted_settings: Settings, hosted_context, base_url: str, expect_secure: bool
):
    app = create_app(hosted_settings, context=hosted_context)
    with TestClient(app, base_url=base_url) as client:
        response = client.post(
            "/api/auth/register",
            json={"email": "a@b.co", "password": "a-good-long-passphrase"},
        )
        assert response.status_code == 201
        header = response.headers["set-cookie"]
        assert "HttpOnly" in header
        assert "SameSite=lax" in header.replace("SameSite=Lax", "SameSite=lax")
        assert ("Secure" in header) is expect_secure
