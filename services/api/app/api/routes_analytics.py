"""Dashboard and learning analytics."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import (
    AssessmentsDep,
    ChunksDep,
    ContextDep,
    CoursesDep,
    DocumentsDep,
    FlashcardsDep,
    MasteryDep,
    SessionDep,
    SessionsDep,
    TopicsDep,
    UserDep,
    require_token,
)
from app.api.routes_tutor import _session_out
from app.api.schemas import (
    DashboardResponse,
    DocumentOut,
    MasteryOut,
    RecommendationOut,
)
from app.learning.mastery import (
    MIN_OBSERVATIONS_FOR_CONFIDENCE,
    WEAK_THRESHOLD,
    recommend_action,
)

router = APIRouter(prefix="/api", tags=["analytics"], dependencies=[Depends(require_token)])


def _mastery_out(estimate) -> MasteryOut:
    probability = float(estimate.probability)
    confident = estimate.observations >= MIN_OBSERVATIONS_FOR_CONFIDENCE
    if not confident:
        label = "unknown"
    elif probability < WEAK_THRESHOLD:
        label = "weak"
    elif probability < 0.85:
        label = "developing"
    else:
        label = "strong"
    out = MasteryOut.model_validate(estimate)
    out.label = label
    out.confident = confident
    return out


@router.get("/analytics/mastery", response_model=list[MasteryOut])
def mastery_list(
    user: UserDep, mastery: MasteryDep, course_id: str | None = None
) -> list[MasteryOut]:
    return [_mastery_out(estimate) for estimate in mastery.list(user.id, course_id)]


@router.get("/analytics/recommendations", response_model=list[RecommendationOut])
def recommendations(
    user: UserDep,
    mastery: MasteryDep,
    topics: TopicsDep,
    flashcards: FlashcardsDep,
    course_id: str | None = None,
) -> list[RecommendationOut]:
    prerequisites = {
        topic.slug: list(topic.prerequisite_ids or []) for topic in topics.list(user.id, course_id)
    }
    items = recommend_action(
        mastery.list(user.id, course_id),
        prerequisites=prerequisites,
        due_flashcards=flashcards.due_count(user.id),
    )
    return [RecommendationOut(**item.__dict__) for item in items[:8]]


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(
    context: ContextDep,
    session: SessionDep,
    user: UserDep,
    courses: CoursesDep,
    documents: DocumentsDep,
    chunks: ChunksDep,
    sessions: SessionsDep,
    flashcards: FlashcardsDep,
    mastery: MasteryDep,
    topics: TopicsDep,
    assessments: AssessmentsDep,
) -> DashboardResponse:
    """One query set for the home screen. Every number is read, never invented."""
    course_rows = courses.list(user.id)
    document_rows = documents.list(user.id)
    estimates = [_mastery_out(estimate) for estimate in mastery.list(user.id)]
    due = flashcards.due_count(user.id)

    prerequisites = {
        topic.slug: list(topic.prerequisite_ids or []) for topic in topics.list(user.id)
    }
    recommendation_items = recommend_action(
        mastery.list(user.id), prerequisites=prerequisites, due_flashcards=due
    )

    attempts = [
        {
            "attempt_id": attempt.id,
            "assessment_id": attempt.assessment_id,
            "score": attempt.score,
            "max_score": attempt.max_score,
            "percentage": round(
                (attempt.score / attempt.max_score * 100.0) if attempt.max_score else 0.0, 1
            ),
            "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
            "weak_topics": (attempt.feedback or {}).get("weak_topics", []),
        }
        for attempt in assessments.attempts_for(user.id, limit=10)
    ]

    return DashboardResponse(
        course_count=len(course_rows),
        document_count=len(document_rows),
        page_count=sum(document.page_count for document in document_rows),
        indexed_chunk_count=chunks.count(user.id),
        due_flashcards=due,
        upcoming_reviews=flashcards.upcoming(user.id, days=7),
        recent_sessions=[_session_out(row, session) for row in sessions.list(user.id, limit=5)],
        recent_documents=[DocumentOut.model_validate(row) for row in document_rows[:5]],
        mastery=estimates,
        weak_topics=[item for item in estimates if item.label == "weak"],
        recommendations=[RecommendationOut(**item.__dict__) for item in recommendation_items[:6]],
        recent_attempts=attempts,
    )
