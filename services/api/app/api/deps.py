"""Request-scoped dependencies and local API authentication.

The local backend is bound to the loopback interface, but a loopback service is
still reachable by any process on the machine, so it is not treated as trusted.
Every route except the liveness probe requires a bearer token that the backend
generates into a 0600 file (or receives from the Tauri shell by environment
variable).  The browser never sees it: the Vite dev server injects the header
server-side, and the desktop shell injects it in Rust.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import Iterator
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth import SESSION_COOKIE, AuthError, TokenService
from app.config import Settings
from app.neon_auth import NeonAuthError, NeonIdentity, local_handle_for
from app.runtime import RuntimeContext
from app.storage.models import User
from app.storage.repositories import (
    AssessmentRepository,
    AssignmentPlanRepository,
    AttentionRepository,
    ChunkRepository,
    CourseRepository,
    DocumentRepository,
    FlashcardRepository,
    MasteryRepository,
    NoteRepository,
    SessionRepository,
    TopicRepository,
    UserRepository,
)

log = logging.getLogger(__name__)

TOKEN_HEADER = "x-studypilot-token"


def get_context(request: Request) -> RuntimeContext:
    context: RuntimeContext | None = getattr(request.app.state, "context", None)
    if context is None:  # pragma: no cover - app always sets this on startup
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Backend is still starting."
        )
    return context


def get_settings_dep(context: Annotated[RuntimeContext, Depends(get_context)]) -> Settings:
    return context.settings


def require_token(
    context: Annotated[RuntimeContext, Depends(get_context)],
    x_studypilot_token: Annotated[str | None, Header(alias=TOKEN_HEADER)] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Reject requests without the local shared secret.

    This guards the *desktop* backend, where the secret proves the caller is
    the Tauri shell's webview rather than another process on the machine. In
    hosted mode there is no such secret and no such boundary — identity comes
    from the session cookie, enforced by :func:`get_current_user` — so this
    check stands down rather than rejecting every request.
    """
    settings = context.settings
    if settings.auth_disabled or settings.is_hosted:
        return
    expected = settings.resolve_api_token()
    supplied = x_studypilot_token
    if not supplied and authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_session(
    context: Annotated[RuntimeContext, Depends(get_context)],
) -> Iterator[Session]:
    with context.database.session() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
ContextDep = Annotated[RuntimeContext, Depends(get_context)]


def _user_from_session(context: RuntimeContext, session: Session, token: str | None) -> User | None:
    """Resolve the signed-in account from a session cookie, if any."""
    if not token:
        return None
    try:
        claims = TokenService(context.settings).verify(token)
    except (AuthError, RuntimeError):
        return None

    user = session.get(User, claims.user_id)
    if user is None or user.email is None:
        return None
    # A bumped version revokes every token issued before it, which is how a
    # password change and "sign out everywhere" take effect without a server
    # side session table.
    if claims.token_version != user.token_version:
        log.info("session_revoked", extra={"user_id": user.id})
        return None
    return user


def _user_from_neon_token(
    context: RuntimeContext, session: Session, authorization: str | None
) -> User | None:
    """Resolve the account from a Neon Auth bearer token.

    Neon owns the credential; this service only verifies the signature and
    maps the immutable subject onto a local row, creating it on first sight so
    a new Neon user needs no separate provisioning step.
    """
    verifier = context.neon_auth
    if verifier is None or not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None

    try:
        identity: NeonIdentity = verifier.verify(token.strip())
    except NeonAuthError as exc:
        log.info("neon_token_rejected", extra={"reason": str(exc)})
        return None

    users = UserRepository(session)
    handle = local_handle_for(identity)
    user = users.get_by_handle(handle)
    if user is None:
        user = users.ensure_local_profile(handle=handle, display_name=identity.display_name)
        user.is_local_profile = False
        log.info("neon_account_linked", extra={"user_id": user.id})
    # Keep the local copy current; Neon remains the source of truth.
    if identity.email and user.email != identity.email:
        user.email = identity.email
    if identity.display_name and user.display_name != identity.display_name:
        user.display_name = identity.display_name
    session.flush()
    return user


def get_current_user_optional(
    context: Annotated[RuntimeContext, Depends(get_context)],
    session: SessionDep,
    studypilot_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> User | None:
    """The acting user, or ``None`` when nobody is signed in.

    Desktop mode always has a user: the single local profile, created on first
    use. Hosted mode resolves identity from whichever provider is configured —
    a Neon Auth bearer token, or this application's own session cookie.
    """
    if not context.settings.is_hosted:
        return UserRepository(session).ensure_local_profile()
    if context.settings.uses_neon_auth:
        return _user_from_neon_token(context, session, authorization)
    return _user_from_session(context, session, studypilot_session)


def current_user_optional():
    """``Depends`` wrapper, so routes can declare an optional user readably."""
    return Depends(get_current_user_optional)


def get_current_user(
    user: Annotated[User | None, Depends(get_current_user_optional)],
) -> User:
    """Require a signed-in user.

    Every repository below takes an explicit ``user_id`` and filters on it, so
    this is the only place the acting identity is decided — and the only place
    that needs to change between the two deployment modes.
    """
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to continue.",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return user


UserDep = Annotated[User, Depends(get_current_user)]
OptionalUserDep = Annotated[User | None, Depends(get_current_user_optional)]


# --- repository providers -------------------------------------------------
def users(session: SessionDep) -> UserRepository:
    return UserRepository(session)


def courses(session: SessionDep) -> CourseRepository:
    return CourseRepository(session)


def documents(session: SessionDep) -> DocumentRepository:
    return DocumentRepository(session)


def chunks(session: SessionDep) -> ChunkRepository:
    return ChunkRepository(session)


def topics(session: SessionDep) -> TopicRepository:
    return TopicRepository(session)


def sessions(session: SessionDep) -> SessionRepository:
    return SessionRepository(session)


def flashcards(session: SessionDep) -> FlashcardRepository:
    return FlashcardRepository(session)


def assessments(session: SessionDep) -> AssessmentRepository:
    return AssessmentRepository(session)


def mastery(session: SessionDep) -> MasteryRepository:
    return MasteryRepository(session)


def notes(session: SessionDep) -> NoteRepository:
    return NoteRepository(session)


def attention(session: SessionDep) -> AttentionRepository:
    return AttentionRepository(session)


UsersDep = Annotated[UserRepository, Depends(users)]
CoursesDep = Annotated[CourseRepository, Depends(courses)]
DocumentsDep = Annotated[DocumentRepository, Depends(documents)]
ChunksDep = Annotated[ChunkRepository, Depends(chunks)]
TopicsDep = Annotated[TopicRepository, Depends(topics)]
SessionsDep = Annotated[SessionRepository, Depends(sessions)]
FlashcardsDep = Annotated[FlashcardRepository, Depends(flashcards)]
AssessmentsDep = Annotated[AssessmentRepository, Depends(assessments)]
MasteryDep = Annotated[MasteryRepository, Depends(mastery)]
NotesDep = Annotated[NoteRepository, Depends(notes)]
AttentionDep = Annotated[AttentionRepository, Depends(attention)]


def plans(session: SessionDep) -> AssignmentPlanRepository:
    return AssignmentPlanRepository(session)


PlansDep = Annotated[AssignmentPlanRepository, Depends(plans)]
