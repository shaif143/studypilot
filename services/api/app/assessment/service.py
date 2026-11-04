"""Assessment and flashcard orchestration.

Generation gathers evidence through the same access-controlled retriever used
by the tutor, so a quiz can only ever be built from material the student owns.
Marking is deterministic first; the knowledge model is updated from the marked
result, not from the model's opinion.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.agents.service import resolve_provider
from app.assessment.generator import GeneratedQuestion, QuestionGenerator, build_blueprint
from app.assessment.grading import grade_response
from app.learning.mastery import recommend_action, schedule_review, update_mastery
from app.rag.retriever import RetrievedChunk
from app.runtime import RuntimeContext
from app.storage.models import (
    Assessment,
    Flashcard,
    Question,
    QuestionResponse,
    UserSettings,
)
from app.storage.repositories import (
    AssessmentRepository,
    ChunkRepository,
    CourseRepository,
    FlashcardRepository,
    MasteryRepository,
    NotFoundError,
    TopicRepository,
)

log = logging.getLogger(__name__)


class NoMaterialError(ValueError):
    """Raised when there is nothing indexed to build an assessment from."""


@dataclass
class GenerationOutcome:
    assessment: Assessment
    questions: list[Question]
    warnings: list[str]


def _gather_evidence(
    *,
    context: RuntimeContext,
    db_session: Any,
    user_id: str,
    course_id: str,
    document_id: str | None,
    topic_slug: str | None,
    page_from: int | None,
    page_to: int | None,
    limit: int,
) -> list[RetrievedChunk]:
    """Collect candidate passages for generation, scoped to what the user owns."""
    chunk_repo = ChunkRepository(db_session)
    course_repo = CourseRepository(db_session)
    course_repo.get(user_id, course_id)  # ownership check

    if document_id:
        rows = chunk_repo.by_document(user_id, document_id, level="child")
    else:
        ids = chunk_repo.scoped_ids(user_id, course_ids=[course_id], level="child")
        rows = chunk_repo.get_many(user_id, ids)

    if topic_slug:
        rows = [row for row in rows if row.topic_slug == topic_slug]
    if page_from is not None:
        rows = [row for row in rows if row.page_number >= page_from]
    if page_to is not None:
        rows = [row for row in rows if row.page_number <= page_to]

    # Prefer longer, prose-like passages; they make better questions.
    rows = [row for row in rows if len(row.text) >= 120]
    rows.sort(key=lambda row: (row.page_number, row.ordinal))
    if len(rows) > limit:
        # Even spread across the selected range rather than the first N pages.
        step = len(rows) / limit
        rows = [rows[int(index * step)] for index in range(limit)]

    titles: dict[str, str] = {}
    from app.storage.repositories import DocumentRepository

    documents = DocumentRepository(db_session)
    result: list[RetrievedChunk] = []
    for row in rows:
        if row.document_id not in titles:
            try:
                titles[row.document_id] = documents.get(user_id, row.document_id).title
            except NotFoundError:
                titles[row.document_id] = ""
        result.append(
            RetrievedChunk(
                chunk_id=row.id,
                text=row.text,
                page_number=row.page_number,
                document_id=row.document_id,
                document_title=titles[row.document_id],
                section=row.section,
                topic_slug=row.topic_slug,
                element_type=row.element_type,
                bbox=row.bbox,
                score=1.0,
                retrievers=["scan"],
                parent_text=row.text,
            )
        )
    return result


def generate_assessment(
    *,
    context: RuntimeContext,
    db_session: Any,
    user_id: str,
    course_id: str,
    document_id: str | None,
    topic_slug: str | None,
    page_from: int | None,
    page_to: int | None,
    question_count: int,
    kinds: list[str],
    difficulty: str,
    kind: str,
    time_limit_seconds: int | None,
    topic_weights: dict[str, float] | None,
    allow_cloud: bool,
    user_settings: UserSettings,
) -> GenerationOutcome:
    evidence = _gather_evidence(
        context=context,
        db_session=db_session,
        user_id=user_id,
        course_id=course_id,
        document_id=document_id,
        topic_slug=topic_slug,
        page_from=page_from,
        page_to=page_to,
        limit=max(question_count * 4, 24),
    )
    if not evidence:
        raise NoMaterialError(
            "There is no indexed text in that selection yet. Upload a PDF with "
            "selectable text, or widen the page range."
        )

    blueprint = build_blueprint(
        question_count=question_count,
        topic_weights=topic_weights,
        difficulty=difficulty,
        kinds=kinds,
    )

    warnings: list[str] = []
    provider, provider_warnings = resolve_provider(
        context.settings, user_settings, requested=None, allow_cloud_request=allow_cloud
    )
    warnings.extend(provider_warnings)

    generator = QuestionGenerator()
    generated: list[GeneratedQuestion] = []
    generator_name = "source_derived"

    if provider.info.generative:
        generated = generator.from_llm(evidence, blueprint=blueprint, provider=provider)
        generator_name = f"llm:{provider.info.key}"
        if not generated:
            warnings.append(
                "The model did not return usable questions, so these were derived "
                "directly from your material instead."
            )
            generator_name = "source_derived"

    if not generated:
        generated = generator.from_source(evidence, blueprint=blueprint)
        if provider.info.generative is False:
            warnings.append(
                "No AI model is configured, so these questions were derived directly "
                "from your slides (fill-in-the-blank from the actual text)."
            )

    if not generated:
        raise NoMaterialError(
            "Could not build questions from that material — the extracted text is too "
            "short or too fragmented."
        )

    assessments = AssessmentRepository(db_session)
    total_marks = sum(question.marks for question in generated)
    title = _assessment_title(kind, topic_slug, document_id, db_session, user_id)
    assessment = assessments.create(
        user_id=user_id,
        course_id=course_id,
        document_id=document_id,
        kind=kind,
        title=title,
        blueprint=blueprint,
        time_limit_seconds=time_limit_seconds,
        total_marks=total_marks,
        generator=generator_name,
    )

    rows = [
        Question(
            assessment_id=assessment.id,
            ordinal=index,
            kind=question.kind,
            prompt=question.prompt,
            options=question.options,
            correct_option=question.correct_option,
            expected_answer=question.expected_answer,
            rubric=question.rubric,
            numeric_answer=question.numeric_answer,
            numeric_tolerance=question.numeric_tolerance,
            marks=question.marks,
            difficulty=question.difficulty,
            topic_slug=question.topic_slug,
            provenance=question.provenance,
            source_chunk_id=question.source_chunk_id,
            source_page=question.source_page,
            explanation=question.explanation,
        )
        for index, question in enumerate(generated)
    ]
    assessments.add_questions(rows)

    log.info(
        "assessment_generated",
        extra={
            "assessment_id": assessment.id,
            "kind": kind,
            "questions": len(rows),
            "generator": generator_name,
        },
    )
    return GenerationOutcome(assessment=assessment, questions=rows, warnings=warnings)


def _assessment_title(
    kind: str, topic_slug: str | None, document_id: str | None, db_session: Any, user_id: str
) -> str:
    label = "Mock exam" if kind == "mock_exam" else "Quiz"
    if topic_slug:
        return f"{label}: {topic_slug.replace('-', ' ')}"
    if document_id:
        from app.storage.repositories import DocumentRepository

        try:
            return f"{label}: {DocumentRepository(db_session).get(user_id, document_id).title}"
        except NotFoundError:
            pass
    return f"{label} — {datetime.now(UTC):%d %b %Y}"


@dataclass
class AttemptOutcome:
    attempt_id: str
    score: float
    max_score: float
    responses: list[dict[str, Any]]
    weak_topics: list[str]
    mastery_changes: list[dict[str, Any]]
    recommended_action: str
    recommendation_reason: str


def submit_attempt(
    *,
    context: RuntimeContext,
    db_session: Any,
    user_id: str,
    assessment_id: str,
    responses: list[dict[str, Any]],
    allow_cloud: bool,
    user_settings: UserSettings,
) -> AttemptOutcome:
    assessments = AssessmentRepository(db_session)
    assessment = assessments.get(user_id, assessment_id)
    questions = {question.id: question for question in assessments.questions(assessment_id)}

    provider, _ = resolve_provider(
        context.settings, user_settings, requested=None, allow_cloud_request=allow_cloud
    )
    marker = provider if provider.info.generative else None

    attempt = assessments.start_attempt(user_id, assessment)
    mastery_repo = MasteryRepository(db_session)
    topics = {
        topic.slug: topic.name
        for topic in TopicRepository(db_session).list(user_id, assessment.course_id)
    }

    rows: list[QuestionResponse] = []
    detail: list[dict[str, Any]] = []
    mastery_changes: list[dict[str, Any]] = []
    total = 0.0
    maximum = 0.0
    topic_credit: dict[str, list[float]] = {}

    for payload in responses:
        question = questions.get(str(payload.get("question_id", "")))
        if question is None:
            continue
        graded = grade_response(
            question,
            selected_option=payload.get("selected_option"),
            answer_text=str(payload.get("answer_text", "")),
            numeric_value=payload.get("numeric_value"),
            llm=marker,
        )
        rows.append(
            QuestionResponse(
                attempt_id=attempt.id,
                question_id=question.id,
                selected_option=payload.get("selected_option"),
                answer_text=str(payload.get("answer_text", "")),
                numeric_value=payload.get("numeric_value"),
                awarded_marks=graded.awarded_marks,
                max_marks=graded.max_marks,
                correct=graded.correct,
                marking_method=graded.marking_method,
                feedback=graded.feedback,
            )
        )
        total += graded.awarded_marks
        maximum += graded.max_marks
        detail.append(
            {
                "question_id": question.id,
                "awarded_marks": graded.awarded_marks,
                "max_marks": graded.max_marks,
                "correct": graded.correct,
                "marking_method": graded.marking_method,
                "feedback": graded.feedback,
                "expected_answer": question.expected_answer,
                "correct_option": question.correct_option,
                "explanation": question.explanation,
                "source_page": question.source_page,
            }
        )

        if question.topic_slug and graded.marking_method != "unmarked":
            topic_credit.setdefault(question.topic_slug, []).append(graded.credit)
            estimate = mastery_repo.get_or_create(
                user_id,
                assessment.course_id,
                question.topic_slug,
                topics.get(question.topic_slug, question.topic_slug),
            )
            update = update_mastery(
                estimate,
                correct=bool(graded.correct),
                question_kind=question.kind,
                partial_credit=None if question.kind in {"mcq", "numeric"} else graded.credit,
            )
            mastery_changes.append(
                {
                    "topic_slug": question.topic_slug,
                    "topic_name": estimate.topic_name,
                    "probability": round(update.probability, 4),
                    "previous_probability": round(update.previous_probability, 4),
                    "label": update.label,
                    "confident": update.confident,
                    "observations": update.observations,
                }
            )

    assessments.save_responses(attempt, rows)
    attempt.score = round(total, 2)
    attempt.max_score = round(maximum, 2)
    attempt.submitted_at = datetime.now(UTC)
    weak_topics = sorted(
        slug
        for slug, credits in topic_credit.items()
        if credits and sum(credits) / len(credits) < 0.6
    )
    attempt.feedback = {"weak_topics": weak_topics, "mastery_changes": mastery_changes}
    db_session.flush()

    flashcards = FlashcardRepository(db_session)
    recommendations = recommend_action(
        mastery_repo.list(user_id, assessment.course_id),
        due_flashcards=flashcards.due_count(user_id),
    )
    top = recommendations[0] if recommendations else None

    log.info(
        "attempt_submitted",
        extra={
            "attempt_id": attempt.id,
            "score": attempt.score,
            "max_score": attempt.max_score,
            "weak_topics": len(weak_topics),
        },
    )

    return AttemptOutcome(
        attempt_id=attempt.id,
        score=attempt.score,
        max_score=attempt.max_score,
        responses=detail,
        weak_topics=weak_topics,
        mastery_changes=mastery_changes,
        recommended_action=top.action if top else "",
        recommendation_reason=top.reason if top else "",
    )


# --- flashcards ----------------------------------------------------------
def generate_flashcards(
    *,
    context: RuntimeContext,
    db_session: Any,
    user_id: str,
    course_id: str,
    document_id: str | None,
    topic_slug: str | None,
    page_from: int | None,
    page_to: int | None,
    count: int,
    allow_cloud: bool,
    user_settings: UserSettings,
) -> tuple[list[Flashcard], list[str]]:
    """Create flashcards from indexed material.

    Provenance is recorded honestly: ``source_derived`` for cards cut straight
    from the slides, ``ai_generated`` when a model wrote them.  Nothing here can
    produce ``past_paper`` — that value only comes from a document the student
    uploaded and labelled as a past paper.
    """
    evidence = _gather_evidence(
        context=context,
        db_session=db_session,
        user_id=user_id,
        course_id=course_id,
        document_id=document_id,
        topic_slug=topic_slug,
        page_from=page_from,
        page_to=page_to,
        limit=max(count * 3, 20),
    )
    if not evidence:
        raise NoMaterialError("There is no indexed text in that selection yet.")

    warnings: list[str] = []
    provider, provider_warnings = resolve_provider(
        context.settings, user_settings, requested=None, allow_cloud_request=allow_cloud
    )
    warnings.extend(provider_warnings)

    generator = QuestionGenerator()
    blueprint = build_blueprint(question_count=count, kinds=["short_answer"])
    generated: list[GeneratedQuestion] = []
    if provider.info.generative:
        generated = generator.from_llm(evidence, blueprint=blueprint, provider=provider)
    if not generated:
        generated = generator.from_source(evidence, blueprint=blueprint)
        if not provider.info.generative:
            warnings.append(
                "Cards were cut directly from your slides because no AI model is configured."
            )

    if not generated:
        raise NoMaterialError("Could not build cards from that material.")

    cards = [
        Flashcard(
            user_id=user_id,
            course_id=course_id,
            document_id=document_id,
            topic_slug=question.topic_slug,
            front=question.prompt,
            back=question.expected_answer or question.explanation,
            difficulty=question.difficulty,
            provenance=question.provenance,
            source_page=question.source_page,
            source_chunk_id=question.source_chunk_id,
            due_at=datetime.now(UTC),
        )
        for question in generated
        if (question.expected_answer or question.explanation)
    ]
    FlashcardRepository(db_session).add_all(cards)
    return cards, warnings


def review_flashcard(*, db_session: Any, user_id: str, card_id: str, rating: int) -> Flashcard:
    repository = FlashcardRepository(db_session)
    card = repository.get(user_id, card_id)
    ease, interval, repetitions, due_at = schedule_review(
        ease=float(card.ease),
        interval_days=int(card.interval_days),
        repetitions=int(card.repetitions),
        rating=rating,
    )
    card.ease = ease
    card.interval_days = interval
    card.repetitions = repetitions
    card.due_at = due_at
    repository.record_review(card, rating, interval)

    # A flashcard review is a weak observation, but it is still evidence.
    if card.topic_slug:
        mastery_repo = MasteryRepository(db_session)
        estimate = mastery_repo.get_or_create(user_id, card.course_id, card.topic_slug)
        update_mastery(estimate, correct=rating >= 2, question_kind="flashcard")
    db_session.flush()
    return card
