"""Account authentication for hosted deployments.

The desktop application has one local profile and a machine-level shared token;
there is nobody to authenticate against. A hosted deployment is a different
problem: without this, every visitor would share one profile and could read
everything anyone uploaded.

Design notes:

* **Argon2id** for password hashing — memory-hard, so a leaked database is
  expensive to attack offline. Parameters are configurable because the right
  cost depends on the container size.
* **Short-ish JWTs in an httpOnly cookie.** The frontend is served from the
  same origin as the API (Vercel rewrites ``/api/*`` to the backend), so a
  ``SameSite=Lax`` cookie is sent with same-site requests and withheld from
  cross-site ones, which removes the need for separate CSRF tokens.
* **A per-user token version.** Bumping it invalidates every outstanding
  session for that user — what "sign out everywhere" and a password change
  both need, without a server-side session table.
"""

from __future__ import annotations

import logging
import re
import secrets
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import Settings

log = logging.getLogger(__name__)

ALGORITHM = "HS256"
SESSION_COOKIE = "studypilot_session"

#: Deliberately permissive: real-world addresses are stranger than most
#: patterns allow, and the authoritative check is whether mail arrives.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 256


class AuthError(Exception):
    """Authentication failed. The message is safe to show a user."""


class RegistrationClosed(AuthError):
    pass


@dataclass(frozen=True)
class SessionClaims:
    user_id: str
    token_version: int
    issued_at: datetime
    expires_at: datetime


def normalise_email(email: str) -> str:
    """Lower-case and trim. Stored this way so lookups are case-insensitive."""
    return email.strip().lower()


def validate_email(email: str) -> str:
    cleaned = normalise_email(email)
    if not _EMAIL_RE.match(cleaned) or len(cleaned) > 320:
        raise AuthError("That does not look like an email address.")
    return cleaned


def validate_password(password: str) -> str:
    """Length-based rules only.

    Composition rules (a digit, a symbol, a capital) push people towards
    predictable substitutions without adding much real strength, so length is
    what is enforced. The upper bound exists because Argon2 cost scales with
    input and an unbounded password is a cheap denial-of-service.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(
            f"Choose a password of at least {MIN_PASSWORD_LENGTH} characters. "
            "A short phrase works well."
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise AuthError(f"Passwords are limited to {MAX_PASSWORD_LENGTH} characters.")
    return password


class PasswordService:
    def __init__(self, settings: Settings) -> None:
        self._hasher = PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_kib,
            parallelism=settings.argon2_parallelism,
        )

    def hash(self, password: str) -> str:
        return self._hasher.hash(validate_password(password))

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            self._hasher.verify(password_hash, password)
        except (VerifyMismatchError, InvalidHashError):
            return False
        except Exception as exc:  # pragma: no cover - corrupt stored hash
            log.warning("password_verify_failed", extra={"reason": type(exc).__name__})
            return False
        return True

    def needs_rehash(self, password_hash: str) -> bool:
        """Whether a stored hash used weaker parameters than the current ones."""
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except Exception:  # pragma: no cover
            return False

    def dummy_verify(self) -> None:
        """Burn comparable time when no account matched.

        Without this, a missing address returns noticeably faster than a wrong
        password, which turns the login endpoint into an account-enumeration
        oracle.
        """
        # Always mismatches; the point is the elapsed time, not the result.
        with suppress(Exception):
            self._hasher.verify(_DUMMY_HASH, "not-the-password")


#: Pre-computed once at import so a failed login pays the same cost as a real
#: one without hashing a throwaway password on every attempt.
_DUMMY_HASH = PasswordHasher().hash(secrets.token_urlsafe(32))


class TokenService:
    def __init__(self, settings: Settings) -> None:
        if not settings.jwt_secret:
            raise RuntimeError("A JWT secret is required to issue sessions.")
        self._secret = settings.jwt_secret
        self._lifetime = timedelta(hours=settings.access_token_hours)

    @property
    def lifetime_seconds(self) -> int:
        return int(self._lifetime.total_seconds())

    def issue(self, user_id: str, token_version: int) -> str:
        now = datetime.now(UTC)
        payload: dict[str, Any] = {
            "sub": user_id,
            "ver": token_version,
            "iat": now,
            "exp": now + self._lifetime,
        }
        return jwt.encode(payload, self._secret, algorithm=ALGORITHM)

    def verify(self, token: str) -> SessionClaims:
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[ALGORITHM],
                options={"require": ["sub", "exp", "iat"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("Your session has expired. Sign in again.") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthError("Your session is not valid. Sign in again.") from exc

        return SessionClaims(
            user_id=str(payload["sub"]),
            token_version=int(payload.get("ver", 0)),
            issued_at=datetime.fromtimestamp(payload["iat"], UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )


def request_is_secure(request: Any) -> bool:
    """Whether this request reached us over TLS.

    Deployments sit behind a TLS-terminating proxy (Vercel, Railway, Fly), so
    the connection to this process is plain HTTP and only the forwarded header
    records the original scheme. Trusting that header is safe here because the
    proxy sets it; a request that arrives directly reports its own scheme.
    """
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    if forwarded:
        return forwarded.lower() == "https"
    return request.url.scheme == "https"


def cookie_settings(settings: Settings, request: Any = None) -> dict[str, Any]:
    """Attributes for the session cookie.

    ``Secure`` follows the request's scheme rather than the deployment mode. A
    browser silently discards a ``Secure`` cookie sent over plain HTTP, so
    pinning it on would make hosted mode impossible to run locally — while
    pinning it off would strip the protection from the deployment that needs
    it. Deriving it per request gets both right.

    ``SameSite=Lax`` suffices because the frontend is served from the same
    origin as the API (the frontend host rewrites ``/api/*`` to this backend),
    so the cookie is withheld from genuine cross-site requests and no separate
    CSRF token is needed.
    """
    secure = True if request is None else request_is_secure(request)
    return {
        "key": SESSION_COOKIE,
        "httponly": True,
        "secure": secure,
        "samesite": "lax",
        "path": "/",
    }
