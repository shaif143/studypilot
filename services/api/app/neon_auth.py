"""Neon Auth (Managed Better Auth) as an identity provider.

Neon Auth is a TypeScript service: the browser signs in through ``@neon/auth``
and receives an EdDSA JWT. This backend is Python, so it participates the only
way a non-JavaScript service can — by verifying that token against the JWKS
Neon publishes, and trusting the ``sub`` claim as the account identity.

This sits *alongside* the built-in email/password system rather than replacing
it, selected by ``STUDYPILOT_AUTH_PROVIDER``. Two reasons:

* The desktop application has no browser to run a JavaScript auth client in,
  and must keep working offline with a local profile.
* Neon's own skill is explicit that a working identity provider should be kept
  unless migration is asked for, and the built-in one is tested and in use.

Tokens are short-lived (15 minutes) and carry no custom claims, so the local
``users`` row is keyed by the Neon subject and created on first sight.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
import jwt
from jwt import PyJWKClient

from app.config import Settings

log = logging.getLogger(__name__)

#: Neon Auth signs with Ed25519. Pinning the algorithm is what stops a token
#: presented as `alg: none`, or symmetrically signed with a public value, from
#: being accepted.
ALGORITHMS = ["EdDSA"]

#: Keys are cached; this bounds how long a rotated-out key stays usable.
JWKS_CACHE_SECONDS = 600


def _origin_of(url: str) -> str | None:
    """Scheme and host of ``url``, which is what Neon puts in `iss` and `aud`."""
    if not url:
        return None
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


class NeonAuthError(Exception):
    """The token was missing, malformed, expired or not signed by Neon."""


@dataclass(frozen=True)
class NeonIdentity:
    """The parts of a verified Neon Auth token this application uses."""

    subject: str
    email: str
    display_name: str
    email_verified: bool


class NeonAuthVerifier:
    """Verifies Neon Auth JWTs against the branch's published JWKS."""

    def __init__(self, settings: Settings) -> None:
        if not settings.neon_auth_jwks_url:
            raise RuntimeError(
                "STUDYPILOT_AUTH_PROVIDER=neon requires NEON_AUTH_JWKS_URL. "
                "Run `neon deploy` (or `neon env pull`) to obtain it."
            )
        self.jwks_url = settings.neon_auth_jwks_url
        # Neon issues tokens with `iss` (and `aud`) set to the *origin* of the
        # Auth service, while NEON_AUTH_BASE_URL carries a path as well
        # (".../neondb/auth"). Comparing the full base URL therefore rejects
        # every genuine token, so the origin is what gets checked.
        self.issuer = _origin_of(settings.neon_auth_base_url or settings.neon_auth_jwks_url)
        self._client: PyJWKClient | None = None
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    def _jwk_client(self) -> PyJWKClient:
        """Return a key client, refreshing it periodically.

        PyJWKClient caches internally; rebuilding it on a timer is what makes
        a key rotation take effect without restarting the service.
        """
        with self._lock:
            expired = time.monotonic() - self._loaded_at > JWKS_CACHE_SECONDS
            if self._client is None or expired:
                self._client = PyJWKClient(self.jwks_url, cache_keys=True)
                self._loaded_at = time.monotonic()
            return self._client

    def verify(self, token: str) -> NeonIdentity:
        if not token:
            raise NeonAuthError("No token supplied.")
        try:
            signing_key = self._jwk_client().get_signing_key_from_jwt(token)
        except Exception as exc:
            # A key that is not in the JWKS is indistinguishable from a forged
            # one, so both land here.
            raise NeonAuthError(f"Could not resolve the signing key: {exc}") from exc

        options: dict[str, Any] = {"require": ["sub", "exp", "iat"]}
        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=ALGORITHMS,
                # Neon sets audience equal to the issuer.
                audience=self.issuer,
                issuer=self.issuer,
                options=options,
            )
        except jwt.ExpiredSignatureError as exc:
            raise NeonAuthError("That session has expired. Sign in again.") from exc
        except jwt.InvalidTokenError as exc:
            raise NeonAuthError(f"That token is not valid: {exc}") from exc

        subject = str(claims.get("sub", ""))
        if not subject:
            raise NeonAuthError("The token carries no subject.")

        email = str(claims.get("email", "")).strip().lower()
        return NeonIdentity(
            subject=subject,
            email=email,
            display_name=str(claims.get("name") or email.split("@")[0] or "Student"),
            email_verified=bool(claims.get("emailVerified", False)),
        )

    def health(self) -> dict[str, Any]:
        """Report whether the JWKS is reachable, for the health endpoint."""
        try:
            response = httpx.get(self.jwks_url, timeout=5)
            response.raise_for_status()
            keys = response.json().get("keys", [])
            return {
                "reachable": True,
                "keys": len(keys),
                "algorithms": sorted({key.get("alg", "?") for key in keys}),
            }
        except Exception as exc:
            return {"reachable": False, "error": str(exc)}


def local_handle_for(identity: NeonIdentity) -> str:
    """The `users.handle` under which a Neon identity is stored locally.

    Namespaced by provider so a Neon subject can never collide with a
    built-in account's email, and stable across email changes because it is
    keyed on the immutable subject.
    """
    return f"neon:{identity.subject}"
