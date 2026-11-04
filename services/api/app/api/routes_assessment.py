"""Quizzes, mock examinations and attempt marking."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import (
    AssessmentsDep,
    ContextDep,
    SessionDep,
    UserDep,
    UsersDep,
    require_token,
)
from app.api.schemas import (
    AssessmentOut,
    AttemptResultOut,
    GradedResponseOut,
    QuestionOut,
    QuizGenerateRequest,
    SubmitRequest,
)
from app.assessment.service import NoMaterialError, generate_assessment, submit_attempt

router = APIRouter(
    prefix="/api/assessments", tags=["assessment"], dependencies=[Depends(require_token)]
)


@router.post("/generate", response_model=AssessmentOut, status_code=status.HTTP_201_CREATED)
def generate(
    payload: QuizGenerateRequest,
    context: ContextDep,
    session: SessionDep,
    user: UserDep,
    users: UsersDep,
) -> AssessmentOut:
    try:
        outcome = generate_assessment(
            context=context,
            db_session=session,
            user_id=user.id,
            course_id=payload.course_id,
            document_id=payload.document_id,
            topic_slug=payload.topic_slug,
            page_from=payload.page_from,
            page_to=payload.page_to,
            question_count=payload.question_count,
            kinds=list(payload.kinds),
            difficulty=payload.difficulty,
            kind=payload.kind,
            time_limit_seconds=payload.time_limit_seconds,
            topic_weights=payload.topic_weights,
            allow_cloud=payload.allow_cloud,
            user_settings=users.settings(user.id),
        )
    except NoMaterialError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    out = AssessmentOut.model_validate(outcome.assessment)
    out.questions = [QuestionOut.model_validate(question) for question in outcome.questions]
    out.warnings = outcome.warnings
    return out


@router.get("", response_model=list[AssessmentOut])
def list_assessments(
    user: UserDep,
    assessments: AssessmentsDep,
    course_id: str | None = None,
    kind: str | None = None,
) -> list[AssessmentOut]:
    rows = assessments.list(user.id, course_id=course_id, kind=kind)
    return [AssessmentOut.model_validate(row) for row in rows]


@router.get("/{assessment_id}", response_model=AssessmentOut)
def get_assessment(assessment_id: str, user: UserDep, assessments: AssessmentsDep) -> AssessmentOut:
    """Return an assessment and its questions.

    Answers, rubrics and correct options are excluded by :class:`QuestionOut`
    so a student cannot read the answer key out of the sit-the-quiz response.
    """
    assessment = assessments.get(user.id, assessment_id)
    out = AssessmentOut.model_validate(assessment)
    out.questions = [
        QuestionOut.model_validate(question) for question in assessments.questions(assessment_id)
    ]
    return out


@router.post("/{assessment_id}/submit", response_model=AttemptResultOut)
def submit(
    assessment_id: str,
    payload: SubmitRequest,
    context: ContextDep,
    session: SessionDep,
    user: UserDep,
    users: UsersDep,
) -> AttemptResultOut:
    outcome = submit_attempt(
        context=context,
        db_session=session,
        user_id=user.id,
        assessment_id=assessment_id,
        responses=[response.model_dump() for response in payload.responses],
        allow_cloud=payload.allow_cloud,
        user_settings=users.settings(user.id),
    )
    percentage = (outcome.score / outcome.max_score * 100.0) if outcome.max_score else 0.0
    return AttemptResultOut(
        attempt_id=outcome.attempt_id,
        assessment_id=assessment_id,
        score=outcome.score,
        max_score=outcome.max_score,
        percentage=round(percentage, 1),
        responses=[GradedResponseOut(**row) for row in outcome.responses],
        weak_topics=outcome.weak_topics,
        mastery_changes=outcome.mastery_changes,
        recommended_action=outcome.recommended_action,
        recommendation_reason=outcome.recommendation_reason,
    )


@router.get("/{assessment_id}/attempts/{attempt_id}", response_model=AttemptResultOut)
def get_attempt(
    assessment_id: str, attempt_id: str, user: UserDep, assessments: AssessmentsDep
) -> AttemptResultOut:
    """Re-read a submitted attempt, including per-question feedback."""
    assessments.get(user.id, assessment_id)
    attempt = assessments.get_attempt(user.id, attempt_id)
    questions = {q.id: q for q in assessments.questions(assessment_id)}
    responses = [
        GradedResponseOut(
            question_id=row.question_id,
            awarded_marks=row.awarded_marks,
            max_marks=row.max_marks,
            correct=row.correct,
            marking_method=row.marking_method,
            feedback=row.feedback,
            expected_answer=questions[row.question_id].expected_answer
            if row.question_id in questions
            else "",
            correct_option=questions[row.question_id].correct_option
            if row.question_id in questions
            else None,
            explanation=questions[row.question_id].explanation
            if row.question_id in questions
            else "",
            source_page=questions[row.question_id].source_page
            if row.question_id in questions
            else None,
        )
        for row in assessments.responses(attempt_id)
    ]
    percentage = (attempt.score / attempt.max_score * 100.0) if attempt.max_score else 0.0
    feedback = attempt.feedback or {}
    return AttemptResultOut(
        attempt_id=attempt.id,
        assessment_id=assessment_id,
        score=attempt.score,
        max_score=attempt.max_score,
        percentage=round(percentage, 1),
        responses=responses,
        weak_topics=list(feedback.get("weak_topics", [])),
        mastery_changes=list(feedback.get("mastery_changes", [])),
    )
