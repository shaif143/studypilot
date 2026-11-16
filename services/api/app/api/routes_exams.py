"""A course's past papers: what they are, and what they say about the next exam."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.agents.service import resolve_provider
from app.api.deps import (
    ContextDep,
    CoursesDep,
    DocumentsDep,
    ExamProfilesDep,
    SessionDep,
    TopicsDep,
    UserDep,
    UsersDep,
    require_token,
)
from app.api.schemas import ExamAnalyseRequest, ExamPaperOut, ExamProfileOut
from app.learning import exam_analysis, exam_service

router = APIRouter(prefix="/api", tags=["exams"], dependencies=[Depends(require_token)])


def _out(
    course_id: str,
    user_id: str,
    session: SessionDep,
    profiles: ExamProfilesDep,
    warnings: list[str] | None = None,
) -> ExamProfileOut:
    papers = exam_service.past_paper_documents(session, user_id, course_id)
    row = profiles.get(user_id, course_id)
    stamp = exam_service.paper_stamp(papers)
    seen = {(entry["document_id"], entry["content_hash"]) for entry in stamp}
    used = {
        (entry.get("document_id", ""), entry.get("content_hash", ""))
        for entry in (row.papers if row else [])
    }
    return ExamProfileOut(
        course_id=course_id,
        papers=[
            ExamPaperOut(
                document_id=paper.id,
                title=paper.title,
                page_count=paper.page_count,
                status=paper.status,
            )
            for paper in papers
        ],
        profile=row.profile if row else None,
        generated_by=row.generated_by if row else "",
        model=row.model if row else "",
        stale=bool(row) and seen != used,
        warnings=warnings or [],
        updated_at=row.updated_at if row else None,
    )


@router.get("/courses/{course_id}/exam-profile", response_model=ExamProfileOut)
def read_exam_profile(
    course_id: str,
    user: UserDep,
    courses: CoursesDep,
    profiles: ExamProfilesDep,
    session: SessionDep,
) -> ExamProfileOut:
    """The course's past papers and the analysis kept for them. Never calls a model."""
    courses.get(user.id, course_id)  # ownership
    return _out(course_id, user.id, session, profiles)


@router.post("/courses/{course_id}/exam-profile", response_model=ExamProfileOut)
def analyse_past_papers(
    course_id: str,
    payload: ExamAnalyseRequest,
    context: ContextDep,
    user: UserDep,
    users: UsersDep,
    courses: CoursesDep,
    documents: DocumentsDep,
    topics: TopicsDep,
    profiles: ExamProfilesDep,
    session: SessionDep,
) -> ExamProfileOut:
    """Read every past paper in the course and forecast the next one.

    Replaces the analysis kept before. Without a model that may read the
    papers, only their format is counted, and the result says so.
    """
    course = courses.get(user.id, course_id)
    papers = exam_service.load_papers(session, user.id, course_id)
    if not papers:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Add at least one past exam paper with readable text first.",
        )
    provider, provider_warnings = resolve_provider(
        context.settings,
        users.settings(user.id),
        requested=None,
        allow_cloud_request=payload.allow_cloud,
    )
    paper_ids = {paper.document_id for paper in papers}
    lectures = [
        doc
        for doc in documents.list(user.id, course_id)
        if doc.id not in paper_ids and doc.kind in {"lecture", "textbook"}
    ]
    lecture_ids = {doc.id for doc in lectures}
    draft = exam_analysis.analyse(
        provider=provider,
        course_title=course.title,
        papers=papers,
        lecture_topics=[
            topic.name
            for topic in topics.list(user.id, course_id)
            if topic.document_id is None or topic.document_id in lecture_ids
        ],
        lecture_titles=[doc.title for doc in lectures],
    )
    profiles.save(
        user.id,
        course_id,
        profile=draft.profile,
        papers=exam_service.paper_stamp(papers),
        generated_by=draft.generated_by,
        model=draft.model,
    )
    return _out(course_id, user.id, session, profiles, provider_warnings + draft.warnings)


@router.delete("/courses/{course_id}/exam-profile", response_model=ExamProfileOut)
def forget_exam_profile(
    course_id: str,
    user: UserDep,
    courses: CoursesDep,
    profiles: ExamProfilesDep,
    session: SessionDep,
) -> ExamProfileOut:
    """Forget the analysis. The papers stay; practice goes back to a generic mix."""
    courses.get(user.id, course_id)
    profiles.delete(user.id, course_id)
    return _out(course_id, user.id, session, profiles)
