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
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import Settings
from app.runtime import RuntimeContext
from app.storage.models import User
from app.storage.repositories import (
    AssessmentRepository,
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
    """Reject requests without the local shared secret."""
    settings = context.settings
    if settings.auth_disabled:
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


def get_current_user(session: SessionDep) -> User:
    """Resolve the acting user.

    On the desktop there is exactly one local profile and it is created on first
    use.  A hosted deployment replaces this dependency with one that validates a
    session cookie or bearer JWT; every repository below already requires an
    explicit user id, so nothing downstream changes.
    """
    return UserRepository(session).ensure_local_profile()


UserDep = Annotated[User, Depends(get_current_user)]


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
