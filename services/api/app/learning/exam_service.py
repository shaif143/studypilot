"""A course's past papers and exam profile, read from and written to the database."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.learning.exam_analysis import Paper
from app.storage.models import Assessment, Document, Question
from app.storage.repositories import DocumentRepository, ExamProfileRepository

#: Statuses with text worth reading.
READABLE = frozenset({"indexed", "partial"})


def past_paper_documents(session: Session, user_id: str, course_id: str) -> list[Document]:
    documents = DocumentRepository(session).list(user_id, course_id)
    papers = [doc for doc in documents if doc.kind == "past_paper" and doc.status in READABLE]
    return sorted(papers, key=lambda doc: doc.title.lower())


def load_papers(session: Session, user_id: str, course_id: str) -> list[Paper]:
    """Every past paper in the course with its full text, in title order."""
    repository = DocumentRepository(session)
    papers = []
    for document in past_paper_documents(session, user_id, course_id):
        text = "\n\n".join(page.text for page in repository.pages(user_id, document.id))
        if text.strip():
            papers.append(
                Paper(
                    document_id=document.id,
                    title=document.title,
                    text=text,
                    content_hash=document.content_hash,
                )
            )
    return papers


def paper_stamp(papers: list[Paper] | list[Document]) -> list[dict[str, str]]:
    """What a profile was drawn from, to tell later whether the papers have changed."""
    return [
        {
            "document_id": getattr(paper, "document_id", None) or getattr(paper, "id", ""),
            "title": paper.title,
            "content_hash": paper.content_hash,
        }
        for paper in papers
    ]


def usable_profile(session: Session, user_id: str, course_id: str) -> dict[str, Any] | None:
    """The course's exam profile, if there is one with something to follow."""
    row = ExamProfileRepository(session).get(user_id, course_id)
    if row is None or not isinstance(row.profile, dict):
        return None
    profile = row.profile
    if not profile.get("topics") and not profile.get("question_mix"):
        return None
    return profile


def earlier_prompts(session: Session, user_id: str, course_id: str, limit: int = 40) -> list[str]:
    """Practice questions already written for this course, newest first."""
    rows = session.execute(
        select(Question.prompt)
        .join(Assessment, Assessment.id == Question.assessment_id)
        .where(Assessment.user_id == user_id, Assessment.course_id == course_id)
        .order_by(Assessment.created_at.desc(), Question.ordinal)
        .limit(limit)
    )
    return [prompt for (prompt,) in rows if prompt]
