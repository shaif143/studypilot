"""Neon Auth token verification.

Neon Auth is a TypeScript service; this Python backend participates by
verifying the EdDSA JWTs it issues against the published JWKS. Everything that
makes that safe is asserted here — the signature, the algorithm, the issuer,
the expiry — because each one, omitted, turns the endpoint into an open door.

No network: a local Ed25519 keypair stands in for Neon's, and the key client
is substituted. The real integration was exercised separately against a live
Neon branch.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import Settings
from app.neon_auth import (
    NeonAuthError,
    NeonAuthVerifier,
    _origin_of,
    local_handle_for,
)

ISSUER = "https://ep-test.neonauth.eu-west-2.aws.neon.tech"
BASE_URL = f"{ISSUER}/neondb/auth"
JWKS_URL = f"{ISSUER}/neondb/auth/.well-known/jwks.json"


@pytest.fixture
def signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
def verifier(signing_key: Ed25519PrivateKey, monkeypatch) -> NeonAuthVerifier:
    instance = NeonAuthVerifier(
        Settings(
            auth_provider="neon",
            neon_auth_base_url=BASE_URL,
            neon_auth_jwks_url=JWKS_URL,
        )
    )
    public_key = signing_key.public_key()
    monkeypatch.setattr(
        instance,
        "_jwk_client",
        lambda: SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=public_key)
        ),
    )
    return instance


def make_token(
    key: Ed25519PrivateKey,
    *,
    issuer: str = ISSUER,
    audience: str = ISSUER,
    subject: str = "0907d978-f63a-4d07-80da-944367b284fe",
    expires_in: int = 900,
    algorithm: str = "EdDSA",
    **extra: object,
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": subject,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
        "email": "student@example.com",
        "name": "A Student",
        "emailVerified": True,
        **extra,
    }
    return jwt.encode(claims, key, algorithm=algorithm)


# --- the issuer bug this file was written for ----------------------------
def test_issuer_is_the_origin_not_the_base_url():
    """Neon's `iss` is the origin; NEON_AUTH_BASE_URL carries a path too.

    Comparing the full base URL rejected every genuine token — found against
    a live Neon branch, where real tokens failed with "Invalid issuer".
    """
    assert _origin_of(BASE_URL) == ISSUER
    assert _origin_of(JWKS_URL) == ISSUER
    assert _origin_of("") is None
    assert _origin_of("not-a-url") is None


def test_verifier_derives_the_issuer_from_either_url():
    from_base = NeonAuthVerifier(
        Settings(auth_provider="neon", neon_auth_base_url=BASE_URL, neon_auth_jwks_url=JWKS_URL)
    )
    assert from_base.issuer == ISSUER

    # The JWKS URL alone is enough; the base URL is not always supplied.
    from_jwks = NeonAuthVerifier(Settings(auth_provider="neon", neon_auth_jwks_url=JWKS_URL))
    assert from_jwks.issuer == ISSUER


# --- acceptance -----------------------------------------------------------
def test_a_genuine_token_is_accepted(verifier, signing_key):
    identity = verifier.verify(make_token(signing_key))

    assert identity.subject == "0907d978-f63a-4d07-80da-944367b284fe"
    assert identity.email == "student@example.com"
    assert identity.display_name == "A Student"
    assert identity.email_verified is True


def test_email_is_normalised_and_a_name_is_derived(verifier, signing_key):
    identity = verifier.verify(make_token(signing_key, email="Student@Example.COM", name=None))
    assert identity.email == "student@example.com"
    assert identity.display_name == "student"


# --- rejection ------------------------------------------------------------
def test_a_token_signed_by_someone_else_is_rejected(verifier):
    impostor = Ed25519PrivateKey.generate()
    with pytest.raises(NeonAuthError):
        verifier.verify(make_token(impostor))


def test_an_expired_token_is_rejected(verifier, signing_key):
    with pytest.raises(NeonAuthError, match="expired"):
        verifier.verify(make_token(signing_key, expires_in=-60))


def test_a_token_from_another_issuer_is_rejected(verifier, signing_key):
    """A token from a different Neon branch must not authenticate here."""
    with pytest.raises(NeonAuthError, match="not valid"):
        verifier.verify(make_token(signing_key, issuer="https://other.neonauth.aws.neon.tech"))


def test_a_token_for_another_audience_is_rejected(verifier, signing_key):
    with pytest.raises(NeonAuthError, match="not valid"):
        verifier.verify(make_token(signing_key, audience="https://somewhere-else"))


def test_a_token_without_a_subject_is_rejected(verifier, signing_key):
    """`sub` is the account identity; without it there is nobody to be."""
    now = datetime.now(UTC)
    token = jwt.encode(
        {"iss": ISSUER, "aud": ISSUER, "iat": now, "exp": now + timedelta(minutes=5)},
        signing_key,
        algorithm="EdDSA",
    )
    with pytest.raises(NeonAuthError):
        verifier.verify(token)


def test_an_unsigned_token_is_rejected(verifier):
    """The classic `alg: none` downgrade."""
    now = datetime.now(UTC)
    token = jwt.encode(
        {"sub": "x", "iss": ISSUER, "aud": ISSUER, "iat": now, "exp": now + timedelta(minutes=5)},
        key="",
        algorithm="none",
    )
    with pytest.raises(NeonAuthError):
        verifier.verify(token)


def test_empty_and_malformed_tokens_are_rejected(verifier):
    for bad in ("", "   ", "not-a-jwt", "aaa.bbb.ccc"):
        with pytest.raises(NeonAuthError):
            verifier.verify(bad)


# --- configuration --------------------------------------------------------
def test_verifier_requires_a_jwks_url():
    with pytest.raises(RuntimeError, match="NEON_AUTH_JWKS_URL"):
        NeonAuthVerifier(Settings(auth_provider="neon"))


def test_hosted_mode_with_neon_auth_needs_no_local_signing_secret(tmp_path):
    """Neon signs the tokens; this service only verifies them."""
    settings = Settings(
        data_dir=tmp_path,
        deployment_mode="hosted",
        auth_provider="neon",
        neon_auth_jwks_url=JWKS_URL,
    )
    assert settings.uses_neon_auth is True
    assert settings.jwt_secret == ""


def test_hosted_mode_with_neon_auth_still_requires_the_jwks_url(tmp_path):
    with pytest.raises(ValueError, match="NEON_AUTH_JWKS_URL"):
        Settings(data_dir=tmp_path, deployment_mode="hosted", auth_provider="neon")


def test_builtin_provider_still_requires_its_own_secret(tmp_path):
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(data_dir=tmp_path, deployment_mode="hosted", auth_provider="builtin")


# --- local account mapping ------------------------------------------------
def test_local_handle_is_namespaced_and_stable():
    """Keyed on the immutable subject, so an email change does not orphan data."""
    identity = SimpleNamespace(subject="abc-123")
    assert local_handle_for(identity) == "neon:abc-123"
    # Cannot collide with a built-in account, whose handle is an email.
    assert not local_handle_for(identity).count("@")
