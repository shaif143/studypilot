"""Hosted-mode accounts, sessions and isolation between users.

The desktop application has one local profile and nothing to sign in to. A
hosted deployment is a different problem entirely: without these guarantees,
every visitor would share one profile and could read everything anyone
uploaded. The isolation tests at the bottom are the ones that matter most.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from conftest import HOSTED_SECRET
from fastapi.testclient import TestClient

from app.auth import (
    SESSION_COOKIE,
    AuthError,
    PasswordService,
    TokenService,
    normalise_email,
    validate_email,
    validate_password,
)
from app.config import Settings
from app.main import create_app

SECRET = HOSTED_SECRET


def register(client: TestClient, email: str, password: str = "a-good-long-passphrase"):
    return client.post("/api/auth/register", json={"email": email, "password": password})


# --- configuration guardrails --------------------------------------------
def test_hosted_mode_refuses_to_start_without_a_signing_secret(tmp_path):
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(data_dir=tmp_path, deployment_mode="hosted")
    with pytest.raises(ValueError, match="at least 32"):
        Settings(data_dir=tmp_path, deployment_mode="hosted", jwt_secret="short")


def test_hosted_mode_refuses_disabled_auth(tmp_path):
    with pytest.raises(ValueError, match="AUTH_DISABLED"):
        Settings(
            data_dir=tmp_path,
            deployment_mode="hosted",
            jwt_secret=SECRET,
            auth_disabled=True,
        )


def test_durability_warnings_name_the_ephemeral_choices(tmp_path):
    warnings = " ".join(
        Settings(
            data_dir=tmp_path,
            deployment_mode="hosted",
            jwt_secret=SECRET,
        ).durability_warnings()
    )
    assert "SQLite" in warnings
    assert "STUDYPILOT_STORAGE_BACKEND=s3" in warnings
    assert "qdrant-remote" in warnings


def test_durability_warnings_clear_once_configured(tmp_path):
    """A properly configured hosted instance must warn about nothing."""
    assert (
        Settings(
            data_dir=tmp_path,
            deployment_mode="hosted",
            jwt_secret=SECRET,
            database_url="postgresql://user:pw@host/db",
            storage_backend="s3",
            s3_bucket="studypilot",
            vector_store="qdrant-remote",
            qdrant_url="https://qdrant.example",
        ).durability_warnings()
        == []
    )


def test_desktop_mode_has_no_durability_warnings(settings: Settings):
    assert settings.durability_warnings() == []


# --- cross-origin access --------------------------------------------------
def test_hosted_mode_ignores_the_development_cors_defaults(tmp_path):
    """localhost must never be able to read a hosted account.

    The defaults exist for the dev server. A deployment that did not ask for
    cross-origin access gets none, however it was started.
    """
    hosted = Settings(data_dir=tmp_path, deployment_mode="hosted", jwt_secret=SECRET)

    assert "http://localhost:5173" in hosted.cors_origins
    assert hosted.effective_cors_origins == ()


def test_hosted_mode_allows_origins_named_explicitly(tmp_path):
    hosted = Settings(
        data_dir=tmp_path,
        deployment_mode="hosted",
        jwt_secret=SECRET,
        cors_origins=("https://studypilot.example",),
    )
    assert hosted.effective_cors_origins == ("https://studypilot.example",)


def test_desktop_mode_keeps_its_defaults(settings: Settings):
    assert "tauri://localhost" in settings.effective_cors_origins


def test_a_wildcard_origin_is_refused(tmp_path):
    """Allowing every site to call an authenticated API is never the intent."""
    with pytest.raises(ValueError, match="not accepted"):
        Settings(data_dir=tmp_path, cors_origins=("*",))


def test_origins_are_read_from_a_comma_separated_variable(monkeypatch, tmp_path):
    """The form a hosting platform's environment field actually takes.

    pydantic-settings decodes a tuple field as JSON by default, so without
    `NoDecode` a plain list of origins fails to start the service — in the
    deploy log, after the image has already built.
    """
    monkeypatch.setenv(
        "STUDYPILOT_CORS_ORIGINS",
        "https://studypilot.example, https://studypilot-preview.example/",
    )
    hosted = Settings(
        data_dir=tmp_path,
        deployment_mode="hosted",
        auth_provider="neon",
        neon_auth_jwks_url="https://auth.example/.well-known/jwks.json",
    )
    assert hosted.effective_cors_origins == (
        "https://studypilot.example",
        "https://studypilot-preview.example",
    )


def test_origins_are_normalised(tmp_path):
    """A trailing slash never matches, and the mismatch is invisible."""
    normalised = Settings(
        data_dir=tmp_path,
        cors_origins=(" https://studypilot.example/ ", ""),
    ).cors_origins
    assert normalised == ("https://studypilot.example",)


def test_cross_origin_with_cookie_sessions_is_called_out(tmp_path):
    """A browser will not send the cookie, so nobody could ever sign in."""
    warnings = " ".join(
        Settings(
            data_dir=tmp_path,
            deployment_mode="hosted",
            jwt_secret=SECRET,
            database_url="postgresql://user:pw@host/db",
            storage_backend="s3",
            s3_bucket="studypilot",
            vector_store="pgvector",
            cors_origins=("https://studypilot.example",),
        ).configuration_warnings()
    )
    assert "cross-origin" in warnings.lower()
    assert "STUDYPILOT_AUTH_PROVIDER=neon" in warnings


def test_cross_origin_with_neon_auth_is_fine(tmp_path):
    """Neon Auth sends a bearer header, which is not a credential."""
    assert (
        Settings(
            data_dir=tmp_path,
            deployment_mode="hosted",
            auth_provider="neon",
            neon_auth_jwks_url="https://auth.example/.well-known/jwks.json",
            database_url="postgresql://user:pw@host/db",
            storage_backend="s3",
            s3_bucket="studypilot",
            vector_store="pgvector",
            cors_origins=("https://studypilot.example",),
        ).configuration_warnings()
        == []
    )


# --- password and email handling -----------------------------------------
def test_passwords_are_hashed_not_stored(hosted_settings: Settings):
    passwords = PasswordService(hosted_settings)
    stored = passwords.hash("a-good-long-passphrase")
    assert "a-good-long-passphrase" not in stored
    assert stored.startswith("$argon2id$")
    assert passwords.verify(stored, "a-good-long-passphrase") is True
    assert passwords.verify(stored, "a-good-long-passphras") is False


def test_identical_passwords_get_different_hashes(hosted_settings: Settings):
    """Salting: two accounts with the same password must not look alike."""
    passwords = PasswordService(hosted_settings)
    assert passwords.hash("the-same-passphrase") != passwords.hash("the-same-passphrase")


def test_corrupt_hash_fails_closed(hosted_settings: Settings):
    passwords = PasswordService(hosted_settings)
    assert passwords.verify("not-a-real-hash", "anything") is False


def test_password_length_rules():
    with pytest.raises(AuthError, match="at least 10"):
        validate_password("short")
    with pytest.raises(AuthError, match="limited to"):
        validate_password("x" * 300)
    assert validate_password("a-good-long-passphrase")


def test_email_normalisation_and_validation():
    assert normalise_email("  Shaif@Example.COM ") == "shaif@example.com"
    assert validate_email("a@b.co") == "a@b.co"
    for bad in ("no-at-sign", "a@b", "@b.co", "a b@c.co", ""):
        with pytest.raises(AuthError):
            validate_email(bad)


# --- tokens ---------------------------------------------------------------
def test_token_round_trip(hosted_settings: Settings):
    tokens = TokenService(hosted_settings)
    claims = tokens.verify(tokens.issue("user-1", 3))
    assert claims.user_id == "user-1"
    assert claims.token_version == 3
    assert claims.expires_at > datetime.now(UTC)


def test_expired_token_is_rejected(hosted_settings: Settings):
    expired = jwt.encode(
        {
            "sub": "user-1",
            "ver": 0,
            "iat": datetime.now(UTC) - timedelta(hours=2),
            "exp": datetime.now(UTC) - timedelta(hours=1),
        },
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(AuthError, match="expired"):
        TokenService(hosted_settings).verify(expired)


def test_token_signed_with_another_secret_is_rejected(hosted_settings: Settings):
    forged = jwt.encode(
        {
            "sub": "user-1",
            "ver": 0,
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(hours=1),
        },
        "a-different-secret-entirely",
        algorithm="HS256",
    )
    with pytest.raises(AuthError, match="not valid"):
        TokenService(hosted_settings).verify(forged)


def test_unsigned_token_is_rejected(hosted_settings: Settings):
    """The classic 'alg: none' downgrade must not be accepted."""
    unsigned = jwt.encode(
        {"sub": "user-1", "exp": datetime.now(UTC) + timedelta(hours=1), "iat": datetime.now(UTC)},
        key="",
        algorithm="none",
    )
    with pytest.raises(AuthError):
        TokenService(hosted_settings).verify(unsigned)


# --- registration and sign-in --------------------------------------------
def test_register_creates_an_account_and_signs_in(hosted: TestClient):
    response = register(hosted, "Shaif@Example.com")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == "shaif@example.com"  # normalised
    assert body["display_name"] == "shaif"
    assert "password" not in response.text
    assert SESSION_COOKIE in response.cookies

    status = hosted.get("/api/auth/status").json()
    assert status["authenticated"] is True
    assert status["requires_authentication"] is True
    assert status["account"]["email"] == "shaif@example.com"


def test_register_rejects_a_duplicate_address(hosted: TestClient):
    assert register(hosted, "a@b.co").status_code == 201
    hosted.post("/api/auth/logout")
    duplicate = register(hosted, "A@B.co")
    assert duplicate.status_code == 409


def test_register_rejects_a_weak_password(hosted: TestClient):
    response = register(hosted, "a@b.co", password="short")
    assert response.status_code == 422
    assert "at least 10" in response.json()["detail"]


def test_login_succeeds_and_fails_indistinguishably(hosted: TestClient):
    register(hosted, "a@b.co", "a-good-long-passphrase")
    hosted.post("/api/auth/logout")

    good = hosted.post(
        "/api/auth/login", json={"email": "a@b.co", "password": "a-good-long-passphrase"}
    )
    assert good.status_code == 200
    hosted.post("/api/auth/logout")

    wrong_password = hosted.post(
        "/api/auth/login", json={"email": "a@b.co", "password": "not-the-password"}
    )
    missing_account = hosted.post(
        "/api/auth/login", json={"email": "nobody@b.co", "password": "not-the-password"}
    )
    # A wrong password and an unknown address must be indistinguishable, or the
    # endpoint becomes an account-enumeration oracle.
    assert wrong_password.status_code == missing_account.status_code == 401
    assert wrong_password.json()["detail"] == missing_account.json()["detail"]


def test_logout_clears_the_session(hosted: TestClient):
    register(hosted, "a@b.co")
    assert hosted.get("/api/courses").status_code == 200
    assert hosted.post("/api/auth/logout").status_code == 204
    assert hosted.get("/api/courses").status_code == 401


def test_registration_can_be_closed(hosted_settings: Settings, hosted_context):
    hosted_settings.allow_registration = False
    with TestClient(
        create_app(hosted_settings, context=hosted_context), base_url="https://testserver"
    ) as client:
        assert register(client, "a@b.co").status_code == 403
        assert client.get("/api/auth/status").json()["registration_open"] is False


# --- session revocation ---------------------------------------------------
def test_logout_everywhere_revokes_outstanding_sessions(hosted: TestClient, hosted_context):
    register(hosted, "a@b.co")
    stolen = hosted.cookies.get(SESSION_COOKIE)
    assert stolen

    assert hosted.post("/api/auth/logout-everywhere").status_code == 204

    # A copy of the old cookie must no longer work.
    with TestClient(
        create_app(hosted_context.settings, context=hosted_context),
        cookies={SESSION_COOKIE: stolen},
        base_url="https://testserver",
    ) as other_device:
        assert other_device.get("/api/courses").status_code == 401


def test_changing_the_password_signs_out_other_devices(hosted: TestClient, hosted_context):
    register(hosted, "a@b.co", "the-original-passphrase")
    old_cookie = hosted.cookies.get(SESSION_COOKIE)

    changed = hosted.post(
        "/api/auth/change-password",
        json={
            "current_password": "the-original-passphrase",
            "new_password": "a-brand-new-passphrase",
        },
    )
    assert changed.status_code == 204
    # This device stays signed in...
    assert hosted.get("/api/courses").status_code == 200

    # ...while the old session elsewhere does not.
    with TestClient(
        create_app(hosted_context.settings, context=hosted_context),
        cookies={SESSION_COOKIE: old_cookie},
        base_url="https://testserver",
    ) as other_device:
        assert other_device.get("/api/courses").status_code == 401

    hosted.post("/api/auth/logout")
    assert (
        hosted.post(
            "/api/auth/login", json={"email": "a@b.co", "password": "a-brand-new-passphrase"}
        ).status_code
        == 200
    )


def test_change_password_requires_the_current_one(hosted: TestClient):
    register(hosted, "a@b.co", "the-original-passphrase")
    response = hosted.post(
        "/api/auth/change-password",
        json={"current_password": "wrong", "new_password": "a-brand-new-passphrase"},
    )
    assert response.status_code == 401


# --- the protected surface -------------------------------------------------
@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/courses"),
        ("get", "/api/documents"),
        ("get", "/api/dashboard"),
        ("get", "/api/settings"),
        ("get", "/api/sessions"),
        ("get", "/api/flashcards"),
        ("get", "/api/notes"),
        ("get", "/api/analytics/mastery"),
        ("get", "/api/privacy/export"),
    ],
)
def test_every_data_route_requires_a_session(hosted: TestClient, method: str, path: str):
    assert getattr(hosted, method)(path).status_code == 401


def test_liveness_stays_open(hosted: TestClient):
    assert hosted.get("/healthz").status_code == 200


def test_the_desktop_shared_token_is_not_accepted_as_identity(hosted: TestClient):
    """The desktop's machine-level secret must not authenticate an account."""
    hosted.headers.update({"x-studypilot-token": "anything-at-all"})
    assert hosted.get("/api/courses").status_code == 401


def test_auth_routes_are_absent_in_desktop_mode(client: TestClient):
    """Offering an account system where there is nothing to sign in to would lie."""
    assert (
        client.post("/api/auth/login", json={"email": "a@b.co", "password": "x"}).status_code == 404
    )
    assert client.get("/api/auth/status").status_code == 404


# --- isolation between accounts -------------------------------------------
def test_accounts_cannot_see_each_others_material(
    hosted: TestClient, hosted_context, sample_pdf_bytes: bytes
):
    """The guarantee a public deployment stands or falls on."""
    # Account one uploads a lecture.
    register(hosted, "one@example.com")
    course = hosted.post("/api/courses", json={"title": "Private course"}).json()
    upload = hosted.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": ("lecture.pdf", sample_pdf_bytes, "application/pdf")},
    )
    assert upload.status_code == 201
    document_id = upload.json()["document"]["id"]
    hosted.post("/api/auth/logout")

    # Account two sees nothing of it.
    with TestClient(
        create_app(hosted_context.settings, context=hosted_context),
        base_url="https://testserver",
    ) as other:
        register(other, "two@example.com")

        assert other.get("/api/courses").json() == []
        assert other.get("/api/documents").json() == []
        assert other.get(f"/api/courses/{course['id']}").status_code == 404
        assert other.get(f"/api/documents/{document_id}").status_code == 404
        assert other.get(f"/api/documents/{document_id}/file").status_code == 404
        assert other.get(f"/api/documents/{document_id}/pages/1").status_code == 404
        assert other.get(f"/api/documents/{document_id}/pages/1/preview").status_code == 404
        assert other.delete(f"/api/documents/{document_id}").status_code == 404

        # Retrieval must find nothing, not merely hide it from the listing.
        answer = other.post(
            "/api/tutor/ask",
            json={"question": "Why is padding necessary?", "course_id": course["id"]},
        )
        assert answer.status_code == 404

        dashboard = other.get("/api/dashboard").json()
        assert dashboard["document_count"] == 0
        assert dashboard["indexed_chunk_count"] == 0


def test_one_accounts_deletion_does_not_touch_another(
    hosted: TestClient, hosted_context, sample_pdf_bytes: bytes
):
    register(hosted, "keeper@example.com")
    course = hosted.post("/api/courses", json={"title": "Kept"}).json()
    hosted.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": ("lecture.pdf", sample_pdf_bytes, "application/pdf")},
    )
    hosted.post("/api/auth/logout")

    with TestClient(
        create_app(hosted_context.settings, context=hosted_context),
        base_url="https://testserver",
    ) as other:
        register(other, "leaver@example.com")
        assert other.delete("/api/privacy/data").status_code == 200

    hosted.post(
        "/api/auth/login",
        json={"email": "keeper@example.com", "password": "a-good-long-passphrase"},
    )
    assert len(hosted.get("/api/courses").json()) == 1
    assert len(hosted.get("/api/documents").json()) == 1
