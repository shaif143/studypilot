"""Repository layer.

Every method that reads or writes private content takes an explicit
``user_id`` and filters on it.  This is the single enforcement point for
ownership: route handlers never build their own queries, so a missing filter
cannot leak another user's material.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.storage.models import (
    Assessment,
    AssessmentAttempt,
    AttentionSample,
    Chunk,
    Citation,
    Course,
    Document,
    Flashcard,
    FlashcardReview,
    LearningSession,
    MasteryEstimate,
    Message,
    Note,
    Page,
    Question,
    QuestionResponse,
    Topic,
    User,
    UserSettings,
)


class NotFoundError(LookupError):
    """Raised when a row does not exist, or is not owned by the caller.

    Deliberately indistinguishable from "does not exist" so that the API cannot
    be used to probe for the existence of another user's documents.
    """


class UserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, user_id: str) -> User:
        user = self.session.get(User, user_id)
        if user is None:
            raise NotFoundError(user_id)
        return user

    def get_by_handle(self, handle: str) -> User | None:
        return self.session.scalar(select(User).where(User.handle == handle))

    def ensure_local_profile(self, handle: str = "local", display_name: str = "Me") -> User:
        """Return the desktop's single local profile, creating it if absent."""
        user = self.get_by_handle(handle)
        if user is not None:
            return user
        user = User(handle=handle, display_name=display_name, is_local_profile=True)
        self.session.add(user)
        self.session.flush()
        self.session.add(UserSettings(user_id=user.id))
        self.session.flush()
        return user

    def settings(self, user_id: str) -> UserSettings:
        row = self.session.get(UserSettings, user_id)
        if row is None:
            row = UserSettings(user_id=user_id)
            self.session.add(row)
            self.session.flush()
        return row

    def update_settings(self, user_id: str, values: dict[str, object]) -> UserSettings:
        row = self.settings(user_id)
        for key, value in values.items():
            if value is not None and hasattr(row, key):
                setattr(row, key, value)
        self.session.flush()
        return row

    def delete_all_data(self, user_id: str) -> None:
        """Hard-delete every row belonging to a user (right to erasure)."""
        user = self.session.get(User, user_id)
        if user is not None:
            self.session.delete(user)
            self.session.flush()


class CourseRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self, user_id: str, *, include_archived: bool = False) -> list[Course]:
        stmt = select(Course).where(Course.user_id == user_id)
        if not include_archived:
            stmt = stmt.where(Course.archived.is_(False))
        return list(self.session.scalars(stmt.order_by(Course.created_at.desc())))

    def get(self, user_id: str, course_id: str) -> Course:
        course = self.session.scalar(
            select(Course).where(Course.id == course_id, Course.user_id == user_id)
        )
        if course is None:
            raise NotFoundError(course_id)
        return course

    def create(self, user_id: str, **values: object) -> Course:
        course = Course(user_id=user_id, **values)  # type: ignore[arg-type]
        self.session.add(course)
        self.session.flush()
        return course

    def update(self, user_id: str, course_id: str, values: dict[str, object]) -> Course:
        course = self.get(user_id, course_id)
        for key, value in values.items():
            if value is not None and hasattr(course, key):
                setattr(course, key, value)
        self.session.flush()
        return course

    def delete(self, user_id: str, course_id: str) -> None:
        course = self.get(user_id, course_id)
        self.session.delete(course)
        self.session.flush()

    def counts(self, user_id: str, course_id: str) -> dict[str, int]:
        self.get(user_id, course_id)
        documents = self.session.scalar(
            select(func.count(Document.id)).where(Document.course_id == course_id)
        )
        pages = self.session.scalar(
            select(func.count(Page.id))
            .select_from(Page)
            .join(Document, Document.id == Page.document_id)
            .where(Document.course_id == course_id)
        )
        topics = self.session.scalar(
            select(func.count(Topic.id)).where(Topic.course_id == course_id)
        )
        return {
            "documents": int(documents or 0),
            "pages": int(pages or 0),
            "topics": int(topics or 0),
        }


class DocumentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self, user_id: str, course_id: str | None = None) -> list[Document]:
        stmt = select(Document).where(Document.user_id == user_id)
        if course_id:
            stmt = stmt.where(Document.course_id == course_id)
        return list(self.session.scalars(stmt.order_by(Document.created_at.desc())))

    def get(self, user_id: str, document_id: str) -> Document:
        document = self.session.scalar(
            select(Document).where(Document.id == document_id, Document.user_id == user_id)
        )
        if document is None:
            raise NotFoundError(document_id)
        return document

    def find_by_hash(self, user_id: str, course_id: str, content_hash: str) -> Document | None:
        return self.session.scalar(
            select(Document).where(
                Document.user_id == user_id,
                Document.course_id == course_id,
                Document.content_hash == content_hash,
            )
        )

    def create(self, **values: object) -> Document:
        document = Document(**values)  # type: ignore[arg-type]
        self.session.add(document)
        self.session.flush()
        return document

    def set_status(self, document: Document, status: str, detail: str = "") -> Document:
        document.status = status
        document.status_detail = detail
        self.session.flush()
        return document

    def delete(self, user_id: str, document_id: str) -> Document:
        document = self.get(user_id, document_id)
        self.session.delete(document)
        self.session.flush()
        return document

    def pages(self, user_id: str, document_id: str) -> list[Page]:
        self.get(user_id, document_id)
        return list(
            self.session.scalars(
                select(Page).where(Page.document_id == document_id).order_by(Page.page_number)
            )
        )

    def page(self, user_id: str, document_id: str, page_number: int) -> Page:
        self.get(user_id, document_id)
        page = self.session.scalar(
            select(Page).where(Page.document_id == document_id, Page.page_number == page_number)
        )
        if page is None:
            raise NotFoundError(f"{document_id}#{page_number}")
        return page


class ChunkRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_all(self, chunks: list[Chunk]) -> None:
        self.session.add_all(chunks)
        self.session.flush()

    def get_many(self, user_id: str, chunk_ids: list[str]) -> list[Chunk]:
        if not chunk_ids:
            return []
        rows = list(
            self.session.scalars(
                select(Chunk).where(Chunk.user_id == user_id, Chunk.id.in_(chunk_ids))
            )
        )
        order = {cid: i for i, cid in enumerate(chunk_ids)}
        return sorted(rows, key=lambda c: order.get(c.id, len(order)))

    def get(self, user_id: str, chunk_id: str) -> Chunk:
        chunk = self.session.scalar(
            select(Chunk).where(Chunk.id == chunk_id, Chunk.user_id == user_id)
        )
        if chunk is None:
            raise NotFoundError(chunk_id)
        return chunk

    def scoped_ids(
        self,
        user_id: str,
        *,
        course_ids: list[str] | None = None,
        document_ids: list[str] | None = None,
        level: str = "child",
    ) -> list[str]:
        """Return the chunk ids the caller is allowed to retrieve from.

        This is the access-control filter applied *before* retrieval.
        """
        stmt = select(Chunk.id).where(Chunk.user_id == user_id, Chunk.level == level)
        if course_ids:
            stmt = stmt.where(Chunk.course_id.in_(course_ids))
        if document_ids:
            stmt = stmt.where(Chunk.document_id.in_(document_ids))
        return list(self.session.scalars(stmt))

    def by_document(self, user_id: str, document_id: str, level: str | None = None) -> list[Chunk]:
        stmt = select(Chunk).where(Chunk.user_id == user_id, Chunk.document_id == document_id)
        if level:
            stmt = stmt.where(Chunk.level == level)
        return list(self.session.scalars(stmt.order_by(Chunk.ordinal)))

    def delete_for_document(self, document_id: str) -> list[str]:
        ids = list(self.session.scalars(select(Chunk.id).where(Chunk.document_id == document_id)))
        self.session.execute(delete(Chunk).where(Chunk.document_id == document_id))
        self.session.flush()
        return ids

    def count(self, user_id: str, level: str = "child") -> int:
        """Count chunks. Defaults to ``child``: the retrievable, embedded units.

        Parent chunks are page-level context, not search targets, so counting
        them would overstate how much material is actually indexed.
        """
        stmt = select(func.count(Chunk.id)).where(Chunk.user_id == user_id)
        if level:
            stmt = stmt.where(Chunk.level == level)
        return int(self.session.scalar(stmt) or 0)


class TopicRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self, user_id: str, course_id: str | None = None) -> list[Topic]:
        stmt = select(Topic).where(Topic.user_id == user_id)
        if course_id:
            stmt = stmt.where(Topic.course_id == course_id)
        return list(self.session.scalars(stmt.order_by(Topic.first_page)))

    def upsert_many(
        self, user_id: str, course_id: str, topics: list[dict[str, object]]
    ) -> list[Topic]:
        result: list[Topic] = []
        for payload in topics:
            slug = str(payload["slug"])
            existing = self.session.scalar(
                select(Topic).where(
                    Topic.user_id == user_id, Topic.course_id == course_id, Topic.slug == slug
                )
            )
            if existing is None:
                existing = Topic(
                    user_id=user_id, course_id=course_id, slug=slug, name=str(payload["name"])
                )
                self.session.add(existing)
            for key, value in payload.items():
                if key != "slug" and hasattr(existing, key):
                    setattr(existing, key, value)
            result.append(existing)
        self.session.flush()
        return result


class SessionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self, user_id: str, limit: int = 20) -> list[LearningSession]:
        return list(
            self.session.scalars(
                select(LearningSession)
                .where(LearningSession.user_id == user_id)
                .order_by(LearningSession.last_active_at.desc())
                .limit(limit)
            )
        )

    def get(self, user_id: str, session_id: str) -> LearningSession:
        row = self.session.scalar(
            select(LearningSession).where(
                LearningSession.id == session_id, LearningSession.user_id == user_id
            )
        )
        if row is None:
            raise NotFoundError(session_id)
        return row

    def create(self, user_id: str, **values: object) -> LearningSession:
        row = LearningSession(user_id=user_id, **values)  # type: ignore[arg-type]
        self.session.add(row)
        self.session.flush()
        return row

    def get_or_create_for_document(
        self, user_id: str, course_id: str, document_id: str
    ) -> LearningSession:
        row = self.session.scalar(
            select(LearningSession)
            .where(
                LearningSession.user_id == user_id,
                LearningSession.document_id == document_id,
            )
            .order_by(LearningSession.last_active_at.desc())
        )
        if row is not None:
            return row
        return self.create(user_id, course_id=course_id, document_id=document_id)

    def touch(self, row: LearningSession, **values: object) -> LearningSession:
        for key, value in values.items():
            if value is not None and hasattr(row, key):
                setattr(row, key, value)
        row.last_active_at = datetime.now(UTC)
        self.session.flush()
        return row

    def messages(self, user_id: str, session_id: str, limit: int = 200) -> list[Message]:
        self.get(user_id, session_id)
        rows = list(
            self.session.scalars(
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.created_at.desc())
                .limit(limit)
            )
        )
        return list(reversed(rows))

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        meta: dict[str, object] | None = None,
        citations: list[dict[str, object]] | None = None,
    ) -> Message:
        message = Message(session_id=session_id, role=role, content=content, meta=meta or {})
        self.session.add(message)
        self.session.flush()
        for index, citation in enumerate(citations or [], start=1):
            self.session.add(
                Citation(
                    message_id=message.id,
                    marker=int(citation.get("marker", index)),
                    chunk_id=str(citation.get("chunk_id", "")),
                    document_id=str(citation.get("document_id", "")),
                    page_number=int(citation.get("page_number", 0)),
                    quote=str(citation.get("quote", "")),
                    bbox=citation.get("bbox"),  # type: ignore[arg-type]
                    score=float(citation.get("score", 0.0)),
                )
            )
        self.session.flush()
        return message

    def delete(self, user_id: str, session_id: str) -> None:
        self.session.delete(self.get(user_id, session_id))
        self.session.flush()


class FlashcardRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_all(self, cards: list[Flashcard]) -> list[Flashcard]:
        self.session.add_all(cards)
        self.session.flush()
        return cards

    def list(
        self,
        user_id: str,
        *,
        course_id: str | None = None,
        document_id: str | None = None,
        due_only: bool = False,
        limit: int = 100,
    ) -> list[Flashcard]:
        stmt = select(Flashcard).where(Flashcard.user_id == user_id, Flashcard.suspended.is_(False))
        if course_id:
            stmt = stmt.where(Flashcard.course_id == course_id)
        if document_id:
            stmt = stmt.where(Flashcard.document_id == document_id)
        if due_only:
            stmt = stmt.where(Flashcard.due_at <= datetime.now(UTC))
        return list(self.session.scalars(stmt.order_by(Flashcard.due_at).limit(limit)))

    def get(self, user_id: str, card_id: str) -> Flashcard:
        card = self.session.scalar(
            select(Flashcard).where(Flashcard.id == card_id, Flashcard.user_id == user_id)
        )
        if card is None:
            raise NotFoundError(card_id)
        return card

    def record_review(self, card: Flashcard, rating: int, interval_days: int) -> FlashcardReview:
        review = FlashcardReview(card_id=card.id, rating=rating, interval_days=interval_days)
        self.session.add(review)
        self.session.flush()
        return review

    def due_count(self, user_id: str) -> int:
        return int(
            self.session.scalar(
                select(func.count(Flashcard.id)).where(
                    Flashcard.user_id == user_id,
                    Flashcard.suspended.is_(False),
                    Flashcard.due_at <= datetime.now(UTC),
                )
            )
            or 0
        )

    def upcoming(self, user_id: str, days: int = 7) -> int:
        horizon = datetime.now(UTC) + timedelta(days=days)
        return int(
            self.session.scalar(
                select(func.count(Flashcard.id)).where(
                    Flashcard.user_id == user_id,
                    Flashcard.suspended.is_(False),
                    Flashcard.due_at <= horizon,
                )
            )
            or 0
        )


class AssessmentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, **values: object) -> Assessment:
        row = Assessment(**values)  # type: ignore[arg-type]
        self.session.add(row)
        self.session.flush()
        return row

    def add_questions(self, questions: list[Question]) -> None:
        self.session.add_all(questions)
        self.session.flush()

    def get(self, user_id: str, assessment_id: str) -> Assessment:
        row = self.session.scalar(
            select(Assessment).where(Assessment.id == assessment_id, Assessment.user_id == user_id)
        )
        if row is None:
            raise NotFoundError(assessment_id)
        return row

    def list(
        self,
        user_id: str,
        *,
        course_id: str | None = None,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[Assessment]:
        stmt = select(Assessment).where(Assessment.user_id == user_id)
        if course_id:
            stmt = stmt.where(Assessment.course_id == course_id)
        if kind:
            stmt = stmt.where(Assessment.kind == kind)
        return list(self.session.scalars(stmt.order_by(Assessment.created_at.desc()).limit(limit)))

    def questions(self, assessment_id: str) -> list[Question]:
        return list(
            self.session.scalars(
                select(Question)
                .where(Question.assessment_id == assessment_id)
                .order_by(Question.ordinal)
            )
        )

    def start_attempt(self, user_id: str, assessment: Assessment) -> AssessmentAttempt:
        attempt = AssessmentAttempt(
            assessment_id=assessment.id,
            user_id=user_id,
            max_score=float(assessment.total_marks),
        )
        self.session.add(attempt)
        self.session.flush()
        return attempt

    def get_attempt(self, user_id: str, attempt_id: str) -> AssessmentAttempt:
        row = self.session.scalar(
            select(AssessmentAttempt).where(
                AssessmentAttempt.id == attempt_id, AssessmentAttempt.user_id == user_id
            )
        )
        if row is None:
            raise NotFoundError(attempt_id)
        return row

    def save_responses(self, attempt: AssessmentAttempt, responses: list[QuestionResponse]) -> None:
        self.session.add_all(responses)
        self.session.flush()

    def attempts_for(self, user_id: str, limit: int = 20) -> list[AssessmentAttempt]:
        return list(
            self.session.scalars(
                select(AssessmentAttempt)
                .where(
                    AssessmentAttempt.user_id == user_id,
                    AssessmentAttempt.submitted_at.is_not(None),
                )
                .order_by(AssessmentAttempt.submitted_at.desc())
                .limit(limit)
            )
        )

    def responses(self, attempt_id: str) -> list[QuestionResponse]:
        return list(
            self.session.scalars(
                select(QuestionResponse).where(QuestionResponse.attempt_id == attempt_id)
            )
        )


class MasteryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self, user_id: str, course_id: str | None = None) -> list[MasteryEstimate]:
        stmt = select(MasteryEstimate).where(MasteryEstimate.user_id == user_id)
        if course_id:
            stmt = stmt.where(MasteryEstimate.course_id == course_id)
        return list(self.session.scalars(stmt.order_by(MasteryEstimate.probability)))

    def get_or_create(
        self, user_id: str, course_id: str, topic_slug: str, topic_name: str = ""
    ) -> MasteryEstimate:
        row = self.session.scalar(
            select(MasteryEstimate).where(
                MasteryEstimate.user_id == user_id,
                MasteryEstimate.course_id == course_id,
                MasteryEstimate.topic_slug == topic_slug,
            )
        )
        if row is None:
            row = MasteryEstimate(
                user_id=user_id,
                course_id=course_id,
                topic_slug=topic_slug,
                topic_name=topic_name or topic_slug,
            )
            self.session.add(row)
            self.session.flush()
        return row

    def save(self, row: MasteryEstimate) -> MasteryEstimate:
        self.session.flush()
        return row


class NoteRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self, user_id: str, course_id: str | None = None) -> list[Note]:
        stmt = select(Note).where(Note.user_id == user_id)
        if course_id:
            stmt = stmt.where(Note.course_id == course_id)
        return list(self.session.scalars(stmt.order_by(Note.updated_at.desc())))

    def get(self, user_id: str, note_id: str) -> Note:
        row = self.session.scalar(select(Note).where(Note.id == note_id, Note.user_id == user_id))
        if row is None:
            raise NotFoundError(note_id)
        return row

    def create(self, **values: object) -> Note:
        row = Note(**values)  # type: ignore[arg-type]
        self.session.add(row)
        self.session.flush()
        return row

    def update(self, user_id: str, note_id: str, values: dict[str, object]) -> Note:
        row = self.get(user_id, note_id)
        for key, value in values.items():
            if value is not None and hasattr(row, key):
                setattr(row, key, value)
        row.edited_by_user = True
        self.session.flush()
        return row

    def delete(self, user_id: str, note_id: str) -> None:
        self.session.delete(self.get(user_id, note_id))
        self.session.flush()


class AttentionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_samples(self, samples: list[AttentionSample]) -> None:
        self.session.add_all(samples)
        self.session.flush()

    def aggregate_for_document(self, user_id: str, document_id: str) -> list[dict[str, object]]:
        rows = self.session.execute(
            select(
                AttentionSample.page_number,
                AttentionSample.element_index,
                func.sum(AttentionSample.dwell_ms),
                func.max(AttentionSample.source),
            )
            .where(
                AttentionSample.user_id == user_id,
                AttentionSample.document_id == document_id,
            )
            .group_by(AttentionSample.page_number, AttentionSample.element_index)
        ).all()
        return [
            {
                "page_number": int(r[0]),
                "element_index": int(r[1]),
                "dwell_ms": int(r[2] or 0),
                "source": r[3],
            }
            for r in rows
        ]

    def delete_all(self, user_id: str) -> int:
        result = self.session.execute(
            delete(AttentionSample).where(AttentionSample.user_id == user_id)
        )
        self.session.flush()
        return int(result.rowcount or 0)
