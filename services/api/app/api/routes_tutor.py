"""Tutor conversation and learning sessions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.agents.service import run_turn
from app.api.deps import (
    ContextDep,
    DocumentsDep,
    SessionDep,
    SessionsDep,
    UserDep,
    UsersDep,
    require_token,
)
from app.api.schemas import (
    AnswerResponse,
    AskRequest,
    CitationOut,
    LearningSessionOut,
    MessageOut,
    SessionUpdate,
    SourceOut,
)
from app.storage.repositories import CourseRepository, NotFoundError

router = APIRouter(prefix="/api", tags=["tutor"], dependencies=[Depends(require_token)])


@router.post("/tutor/ask", response_model=AnswerResponse)
def ask(
    payload: AskRequest,
    context: ContextDep,
    session: SessionDep,
    user: UserDep,
    users: UsersDep,
) -> AnswerResponse:
    """Run one tutor turn: retrieve, answer, cite, recommend."""
    user_settings = users.settings(user.id)
    result = run_turn(
        context=context,
        db_session=session,
        user_id=user.id,
        question=payload.question,
        mode=payload.mode,
        course_id=payload.course_id,
        document_id=payload.document_id,
        session_id=payload.session_id,
        page_number=payload.page_number,
        explanation_level=payload.explanation_level,
        hint_level=payload.hint_level,
        allow_cloud=payload.allow_cloud,
        requested_provider=payload.provider,
        user_settings=user_settings,
    )
    state = result.state
    return AnswerResponse(
        session_id=result.session_id,
        message_id=result.message_id,
        answer=state.get("answer", ""),
        citations=[CitationOut(**citation) for citation in state.get("citations") or []],
        sources=[
            SourceOut(**{key: value for key, value in source.items() if key != "parent_text"})
            for source in state.get("retrieved_sources") or []
        ],
        grounded=bool(state.get("grounded")),
        generative=bool(state.get("generative")),
        abstained=bool(state.get("abstained")),
        provider=str(state.get("provider", "")),
        model=str(state.get("model", "")),
        query_class=str(state.get("query_class", "")),
        evidence_sufficient=bool(state.get("evidence_sufficient", False)),
        evidence_note=str(state.get("evidence_note", "")),
        recommended_action=str(state.get("recommended_action", "")),
        recommendation_reason=str(state.get("recommendation_reason", "")),
        input_tokens=state.get("input_tokens"),
        output_tokens=state.get("output_tokens"),
        warnings=result.warnings,
    )


def _session_out(row, session) -> LearningSessionOut:
    out = LearningSessionOut.model_validate(row)
    if row.document_id:
        from app.storage.models import Document

        document = session.get(Document, row.document_id)
        if document is not None:
            out.document_title = document.title
    if row.course_id:
        from app.storage.models import Course

        course = session.get(Course, row.course_id)
        if course is not None:
            out.course_title = course.title
    return out


@router.get("/sessions", response_model=list[LearningSessionOut])
def list_sessions(
    session: SessionDep, user: UserDep, sessions: SessionsDep, limit: int = 20
) -> list[LearningSessionOut]:
    return [_session_out(row, session) for row in sessions.list(user.id, limit)]


@router.get("/sessions/{session_id}", response_model=LearningSessionOut)
def get_session_detail(
    session_id: str, session: SessionDep, user: UserDep, sessions: SessionsDep
) -> LearningSessionOut:
    return _session_out(sessions.get(user.id, session_id), session)


@router.post("/sessions/resume", response_model=LearningSessionOut)
def resume_session(
    document_id: str,
    session: SessionDep,
    user: UserDep,
    sessions: SessionsDep,
    documents: DocumentsDep,
) -> LearningSessionOut:
    """Return the saved session for a document, creating one on first open."""
    document = documents.get(user.id, document_id)
    row = sessions.get_or_create_for_document(user.id, document.course_id, document_id)
    return _session_out(row, session)


@router.patch("/sessions/{session_id}", response_model=LearningSessionOut)
def update_session(
    session_id: str,
    payload: SessionUpdate,
    session: SessionDep,
    user: UserDep,
    sessions: SessionsDep,
) -> LearningSessionOut:
    """Persist reading position so the session resumes where it left off."""
    row = sessions.get(user.id, session_id)
    sessions.touch(row, **payload.model_dump(exclude_unset=True))
    return _session_out(row, session)


@router.get("/sessions/{session_id}/messages", response_model=list[MessageOut])
def list_messages(
    session_id: str, user: UserDep, sessions: SessionsDep, limit: int = 200
) -> list[MessageOut]:
    rows = sessions.messages(user.id, session_id, limit)
    return [
        MessageOut(
            id=row.id,
            role=row.role,
            content=row.content,
            created_at=row.created_at,
            meta=row.meta or {},
            citations=[
                CitationOut(
                    marker=citation.marker,
                    chunk_id=citation.chunk_id,
                    document_id=citation.document_id,
                    page_number=citation.page_number,
                    bbox=citation.bbox,
                    score=citation.score,
                    quote=citation.quote,
                )
                for citation in sorted(row.citations, key=lambda c: c.marker)
            ],
        )
        for row in rows
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(session_id: str, user: UserDep, sessions: SessionsDep) -> None:
    sessions.delete(user.id, session_id)


@router.get("/courses/{course_id}/topics", response_model=list[dict])
def course_topics(course_id: str, session: SessionDep, user: UserDep) -> list[dict]:
    from app.storage.repositories import TopicRepository

    CourseRepository(session).get(user.id, course_id)
    try:
        topics = TopicRepository(session).list(user.id, course_id)
    except NotFoundError:
        topics = []
    return [
        {
            "id": topic.id,
            "name": topic.name,
            "slug": topic.slug,
            "summary": topic.summary,
            "document_id": topic.document_id,
            "first_page": topic.first_page,
            "last_page": topic.last_page,
        }
        for topic in topics
    ]
