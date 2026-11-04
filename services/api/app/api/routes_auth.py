"""Account registration and sign-in for hosted deployments.

These routes exist only in hosted mode. The desktop application has one local
profile and nothing to sign in to, so mounting them there would offer an
account system that means nothing.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import ContextDep, SessionDep, UsersDep, current_user_optional
from app.auth import (
    AuthError,
    PasswordService,
    TokenService,
    cookie_settings,
    request_is_secure,
    validate_email,
    validate_password,
)
from app.storage.models import User, UserSettings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)
    display_name: str = Field(default="", max_length=256)


class LoginRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)


class AccountOut(BaseModel):
    id: str
    email: str
    display_name: str


class AuthStatusOut(BaseModel):
    authenticated: bool
    #: False in desktop mode, where there is nothing to sign in to.
    requires_authentication: bool
    registration_open: bool
    account: AccountOut | None = None


def _issue_session(
    response: Response,
    request: Request,
    context,
    user: User,
) -> None:
    tokens = TokenService(context.settings)
    response.set_cookie(
        value=tokens.issue(user.id, user.token_version),
        max_age=tokens.lifetime_seconds,
        **cookie_settings(context.settings, request),
    )
    if context.settings.is_hosted and not request_is_secure(request):
        # Worth saying loudly: without TLS the session cookie travels in clear
        # text, and the browser will not mark it Secure.
        log.warning(
            "insecure_session_issued",
            extra={"detail": "a hosted instance issued a session over plain HTTP"},
        )


@router.get("/status", response_model=AuthStatusOut)
def status_(context: ContextDep, user: User | None = current_user_optional()) -> AuthStatusOut:
    """What the frontend needs to decide whether to show a sign-in screen."""
    return AuthStatusOut(
        authenticated=user is not None,
        requires_authentication=context.settings.is_hosted,
        registration_open=context.settings.allow_registration,
        account=(
            AccountOut(id=user.id, email=user.email or "", display_name=user.display_name)
            if user
            else None
        ),
    )


@router.post("/register", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    context: ContextDep,
    session: SessionDep,
    users: UsersDep,
) -> AccountOut:
    if not context.settings.allow_registration:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is closed on this instance.",
        )
    try:
        email = validate_email(payload.email)
        validate_password(payload.password)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    existing = session.scalar(select(User).where(User.email == email))
    if existing is not None:
        # Registration necessarily reveals that an address is taken; there is
        # no way to create an account without it. Sign-in does not.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account already exists for that email address.",
        )

    passwords = PasswordService(context.settings)
    user = User(
        handle=email,
        display_name=payload.display_name.strip() or email.split("@")[0],
        is_local_profile=False,
        email=email,
        password_hash=passwords.hash(payload.password),
        last_login_at=datetime.now(UTC),
    )
    session.add(user)
    session.flush()
    session.add(UserSettings(user_id=user.id))
    session.flush()

    _issue_session(response, request, context, user)
    log.info("account_registered", extra={"user_id": user.id})
    return AccountOut(id=user.id, email=email, display_name=user.display_name)


@router.post("/login", response_model=AccountOut)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    context: ContextDep,
    session: SessionDep,
) -> AccountOut:
    passwords = PasswordService(context.settings)
    email = payload.email.strip().lower()
    user = session.scalar(select(User).where(User.email == email))

    if user is None or not user.password_hash:
        # Spend comparable time so a missing address is not distinguishable
        # from a wrong password by response latency.
        passwords.dummy_verify()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="That email address and password do not match.",
        )

    if not passwords.verify(user.password_hash, payload.password):
        log.info("login_failed", extra={"user_id": user.id})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="That email address and password do not match.",
        )

    # Upgrade the stored hash if the cost parameters have since increased.
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash(payload.password)

    user.last_login_at = datetime.now(UTC)
    session.flush()

    _issue_session(response, request, context, user)
    log.info("login_succeeded", extra={"user_id": user.id})
    return AccountOut(id=user.id, email=user.email or "", display_name=user.display_name)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, context: ContextDep) -> None:
    response.delete_cookie(**cookie_settings(context.settings, request))


@router.post("/logout-everywhere", status_code=status.HTTP_204_NO_CONTENT)
def logout_everywhere(
    request: Request,
    response: Response,
    context: ContextDep,
    session: SessionDep,
    user: User | None = current_user_optional(),
) -> None:
    """Invalidate every outstanding session for this account."""
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in.")
    user.token_version += 1
    session.flush()
    response.delete_cookie(**cookie_settings(context.settings, request))
    log.info("sessions_revoked", extra={"user_id": user.id})


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    response: Response,
    context: ContextDep,
    session: SessionDep,
    user: User | None = current_user_optional(),
) -> None:
    if user is None or not user.password_hash:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in.")

    passwords = PasswordService(context.settings)
    if not passwords.verify(user.password_hash, payload.current_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Your current password is wrong."
        )
    try:
        user.password_hash = passwords.hash(payload.new_password)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    # A password change signs out other devices; this one stays signed in.
    user.token_version += 1
    session.flush()
    _issue_session(response, request, context, user)
    log.info("password_changed", extra={"user_id": user.id})


def count_accounts(session) -> int:
    return int(session.scalar(select(func.count(User.id)).where(User.email.is_not(None))) or 0)
