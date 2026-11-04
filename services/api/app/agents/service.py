"""Tutor turn orchestration.

Builds the per-request graph dependencies (including the consent-gated model
provider), runs the graph, and persists the turn so the session can be resumed
exactly as the student left it.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from app.agents.graph import GraphDependencies, build_learning_graph
from app.agents.state import new_state
from app.config import Settings
from app.models.base import ConsentRequiredError, ProviderUnavailableError
from app.models.llm import ExtractiveProvider
from app.models.registry import get_llm_provider
from app.rag.retriever import HybridRetriever
from app.runtime import RuntimeContext
from app.storage.models import LearningSession, UserSettings
from app.storage.repositories import (
    ChunkRepository,
    CourseRepository,
    DocumentRepository,
    NotFoundError,
    SessionRepository,
)

log = logging.getLogger(__name__)


@dataclass
class TurnResult:
    session_id: str
    message_id: str
    state: dict[str, Any]
    warnings: list[str]


def build_retriever(context: RuntimeContext, session: Any) -> HybridRetriever:
    return HybridRetriever(
        settings=context.settings,
        embedder=context.embedder,
        vector_store=context.vector_store,
        sparse_index=context.sparse_index,
        chunks=ChunkRepository(session),
        documents=DocumentRepository(session),
    )


def resolve_provider(
    settings: Settings,
    user_settings: UserSettings,
    *,
    requested: str | None,
    allow_cloud_request: bool,
) -> tuple[Any, list[str]]:
    """Pick a generation provider, honouring the two-part consent gate.

    Cloud use requires *both* the stored preference and an explicit flag on this
    request.  If the choice cannot be honoured, fall back to the offline
    extractive provider and return a warning rather than failing the turn.
    """
    warnings: list[str] = []
    consent = bool(user_settings.allow_cloud_processing and allow_cloud_request)
    try:
        return get_llm_provider(settings, cloud_consent=consent, requested=requested), warnings
    except ConsentRequiredError as exc:
        warnings.append(str(exc))
    except ProviderUnavailableError as exc:
        warnings.append(f"{exc} Falling back to source quoting.")
    return ExtractiveProvider(), warnings


def run_turn(
    *,
    context: RuntimeContext,
    db_session: Any,
    user_id: str,
    question: str,
    mode: str,
    course_id: str | None,
    document_id: str | None,
    session_id: str | None,
    page_number: int | None,
    explanation_level: str | None,
    hint_level: int,
    allow_cloud: bool,
    requested_provider: str | None,
    user_settings: UserSettings,
) -> TurnResult:
    settings = context.settings
    sessions = SessionRepository(db_session)
    documents = DocumentRepository(db_session)
    courses = CourseRepository(db_session)

    # --- resolve and authorise the scope --------------------------------
    learning_session: LearningSession
    if session_id:
        learning_session = sessions.get(user_id, session_id)
        course_id = course_id or learning_session.course_id
        document_id = document_id or learning_session.document_id
    elif document_id:
        document = documents.get(user_id, document_id)  # ownership check
        course_id = document.course_id
        learning_session = sessions.get_or_create_for_document(user_id, course_id, document_id)
    elif course_id:
        courses.get(user_id, course_id)  # ownership check
        learning_session = sessions.create(user_id, course_id=course_id)
    else:
        raise NotFoundError("A course or document is required.")

    if document_id:
        documents.get(user_id, document_id)
    if course_id:
        course = courses.get(user_id, course_id)
        integrity_policy = course.integrity_policy
    else:
        integrity_policy = ""

    page = page_number or learning_session.current_page or 1
    topic = learning_session.current_topic
    if document_id:
        # The page heading is the best available topic label for this turn.
        with suppress(NotFoundError):
            topic = documents.page(user_id, document_id, page).heading or topic

    provider, provider_warnings = resolve_provider(
        settings,
        user_settings,
        requested=requested_provider,
        allow_cloud_request=allow_cloud,
    )

    dependencies = GraphDependencies(
        settings=settings,
        retriever=build_retriever(context, db_session),
        llm=provider,
        integrity_policy=integrity_policy,
        cloud_consent=bool(user_settings.allow_cloud_processing and allow_cloud),
    )
    graph = build_learning_graph(dependencies)

    state = new_state(
        session_id=learning_session.id,
        user_id=user_id,
        course_id=course_id or "",
        document_id=document_id or "",
        student_question=question,
        current_page=page,
        current_topic=topic or "",
        learning_mode=mode,  # type: ignore[arg-type]
        explanation_level=explanation_level or user_settings.explanation_level,
        assignment_policy=user_settings.assignment_policy,
        hint_level=hint_level,
    )

    # --- persist the student's turn before running the graph -------------
    if question:
        sessions.add_message(learning_session.id, "user", question, {"mode": mode})

    result_state = dict(graph.invoke(state))
    warnings = provider_warnings + list(result_state.get("warnings") or [])

    message = sessions.add_message(
        learning_session.id,
        "assistant",
        result_state.get("answer", ""),
        {
            "provider": result_state.get("provider", ""),
            "model": result_state.get("model", ""),
            "grounded": bool(result_state.get("grounded")),
            "generative": bool(result_state.get("generative")),
            "abstained": bool(result_state.get("abstained")),
            "mode": mode,
            "query_class": result_state.get("query_class", ""),
            "evidence_sufficient": bool(result_state.get("evidence_sufficient", False)),
            "input_tokens": result_state.get("input_tokens"),
            "output_tokens": result_state.get("output_tokens"),
            "warnings": warnings,
        },
        citations=result_state.get("citations") or [],
    )

    sessions.touch(
        learning_session,
        current_page=page,
        current_topic=topic or "",
        learning_mode=mode,
        recommended_action=result_state.get("recommended_action", ""),
    )

    log.info(
        "tutor_turn",
        extra={
            "session_id": learning_session.id,
            "mode": mode,
            "provider": result_state.get("provider"),
            "grounded": result_state.get("grounded"),
            "source_count": len(result_state.get("retrieved_sources") or []),
        },
    )

    result_state["warnings"] = warnings
    return TurnResult(
        session_id=learning_session.id,
        message_id=message.id,
        state=result_state,
        warnings=warnings,
    )
