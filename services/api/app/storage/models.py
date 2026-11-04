"""SQLAlchemy ORM models.

The schema is deliberately provider-neutral (no SQLite-only column types) so the
same models can back a hosted PostgreSQL deployment.  SQLite-specific features
(FTS5 for sparse retrieval) live in :mod:`app.rag.sparse` behind an interface.

Ownership is modelled explicitly: every row that can contain private academic
content carries a ``user_id`` so authorisation can be enforced in the repository
layer rather than relying on callers to filter correctly.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSON, list[Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class User(Base, TimestampMixin):
    """A local profile (desktop) or an authenticated account (hosted)."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    handle: Mapped[str] = mapped_column(String(128), unique=True)
    display_name: Mapped[str] = mapped_column(String(256), default="")
    is_local_profile: Mapped[bool] = mapped_column(Boolean, default=True)

    # --- hosted accounts -------------------------------------------------
    #: Null for the desktop's local profile, which has nothing to sign in to.
    #: Stored lower-cased so lookups are case-insensitive.
    email: Mapped[str | None] = mapped_column(String(320), unique=True, default=None)
    #: Argon2id hash. Never a password, and never logged.
    password_hash: Mapped[str | None] = mapped_column(String(512), default=None)
    #: Bumping this invalidates every outstanding session for the user, which
    #: is what a password change and "sign out everywhere" both need.
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    courses: Mapped[list[Course]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    settings: Mapped[UserSettings | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


class UserSettings(Base, TimestampMixin):
    """Per-user preferences, including the privacy and consent controls."""

    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    theme: Mapped[str] = mapped_column(String(16), default="system")
    learning_style: Mapped[str] = mapped_column(String(32), default="balanced")
    explanation_level: Mapped[str] = mapped_column(String(16), default="intermediate")
    #: Guidance policy for the assignment coach: "socratic" | "explain" | "off".
    assignment_policy: Mapped[str] = mapped_column(String(16), default="socratic")
    quiz_checkpoint_slides: Mapped[int] = mapped_column(Integer, default=5)

    # Privacy / consent. All default to the most private option.
    allow_cloud_processing: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_cloud_vision: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_external_search: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_telemetry: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_attention_tracking: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_federated_participation: Mapped[bool] = mapped_column(Boolean, default=False)
    retention_days: Mapped[int | None] = mapped_column(Integer, default=None)

    user: Mapped[User] = relationship(back_populates="settings")


class Course(Base, TimestampMixin):
    __tablename__ = "courses"
    __table_args__ = (Index("ix_courses_user", "user_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(256))
    code: Mapped[str] = mapped_column(String(64), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    institution: Mapped[str] = mapped_column(String(256), default="")
    #: Institution-specific academic-integrity note shown to the assignment coach.
    integrity_policy: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User] = relationship(back_populates="courses")
    documents: Mapped[list[Document]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    topics: Mapped[list[Topic]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )


class Document(Base, TimestampMixin):
    """An uploaded source file plus its ingestion state."""

    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_course", "course_id"),
        Index("ix_documents_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"))
    course_id: Mapped[str] = mapped_column(String(32), ForeignKey("courses.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(512))
    original_filename: Mapped[str] = mapped_column(String(512))
    media_type: Mapped[str] = mapped_column(String(128), default="application/pdf")
    #: SHA-256 of the file bytes; also the blob-store key.
    content_hash: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    #: "pending" | "processing" | "indexed" | "partial" | "failed"
    status: Mapped[str] = mapped_column(String(16), default="pending")
    status_detail: Mapped[str] = mapped_column(Text, default="")
    #: Which document kinds are present: lecture | past_paper | assignment | textbook
    kind: Mapped[str] = mapped_column(String(32), default="lecture")
    embedding_model: Mapped[str] = mapped_column(String(128), default="")
    extraction_meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    course: Mapped[Course] = relationship(back_populates="documents")
    pages: Mapped[list[Page]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Page.page_number"
    )
    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Page(Base):
    """One page (PDF) or slide (PPTX) with its extracted elements."""

    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("document_id", "page_number", name="uq_pages_doc_number"),
        Index("ix_pages_document", "document_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("documents.id", ondelete="CASCADE")
    )
    page_number: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str] = mapped_column(String(512), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    width: Mapped[float] = mapped_column(Float, default=0.0)
    height: Mapped[float] = mapped_column(Float, default=0.0)
    preview_path: Mapped[str] = mapped_column(String(512), default="")
    #: Extracted elements with bounding boxes: see app.ingestion.pdf.PageElement.
    elements: Mapped[list[Any]] = mapped_column(JSON, default=list)
    has_equations: Mapped[bool] = mapped_column(Boolean, default=False)
    has_figures: Mapped[bool] = mapped_column(Boolean, default=False)
    has_tables: Mapped[bool] = mapped_column(Boolean, default=False)

    document: Mapped[Document] = relationship(back_populates="pages")


class Topic(Base, TimestampMixin):
    """A concept detected in course material, used for mastery and navigation."""

    __tablename__ = "topics"
    __table_args__ = (Index("ix_topics_course", "course_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(String(32), ForeignKey("courses.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(256))
    slug: Mapped[str] = mapped_column(String(256))
    summary: Mapped[str] = mapped_column(Text, default="")
    document_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("documents.id", ondelete="CASCADE"), default=None
    )
    first_page: Mapped[int] = mapped_column(Integer, default=0)
    last_page: Mapped[int] = mapped_column(Integer, default=0)
    #: Ids of topics that are prerequisites for this one.
    prerequisite_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)

    course: Mapped[Course] = relationship(back_populates="topics")


class Chunk(Base):
    """A retrievable unit of content with full provenance.

    Parent chunks carry page-level context used for answer generation; child
    chunks are the smaller units that are embedded and matched.
    """

    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_document", "document_id"),
        Index("ix_chunks_user", "user_id"),
        Index("ix_chunks_course", "course_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"))
    course_id: Mapped[str] = mapped_column(String(32), ForeignKey("courses.id", ondelete="CASCADE"))
    document_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("documents.id", ondelete="CASCADE")
    )
    parent_id: Mapped[str | None] = mapped_column(String(32), default=None)
    #: "parent" | "child"
    level: Mapped[str] = mapped_column(String(8), default="child")
    page_number: Mapped[int] = mapped_column(Integer, default=0)
    #: Inclusive page span; equals page_number for single-page chunks.
    page_end: Mapped[int] = mapped_column(Integer, default=0)
    section: Mapped[str] = mapped_column(String(512), default="")
    topic_slug: Mapped[str] = mapped_column(String(256), default="")
    #: "text" | "heading" | "table" | "figure" | "equation"
    element_type: Mapped[str] = mapped_column(String(32), default="text")
    text: Mapped[str] = mapped_column(Text)
    #: [x0, y0, x1, y1] in PDF points, for in-viewer highlighting.
    bbox: Mapped[list[Any] | None] = mapped_column(JSON, default=None)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)
    document_version: Mapped[int] = mapped_column(Integer, default=1)
    embedding_model: Mapped[str] = mapped_column(String(128), default="")
    ordinal: Mapped[int] = mapped_column(Integer, default=0)

    document: Mapped[Document] = relationship(back_populates="chunks")


class LearningSession(Base, TimestampMixin):
    """A resumable study session; the persisted projection of the agent state."""

    __tablename__ = "learning_sessions"
    __table_args__ = (Index("ix_sessions_user", "user_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"))
    course_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("courses.id", ondelete="CASCADE"), default=None
    )
    document_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("documents.id", ondelete="CASCADE"), default=None
    )
    current_page: Mapped[int] = mapped_column(Integer, default=1)
    current_topic: Mapped[str] = mapped_column(String(256), default="")
    learning_mode: Mapped[str] = mapped_column(String(32), default="read")
    recommended_action: Mapped[str] = mapped_column(String(64), default="")
    slides_since_checkpoint: Mapped[int] = mapped_column(Integer, default=0)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    messages: Mapped[list[Message]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="Message.created_at"
    )


class Message(Base):
    """One turn of a tutor conversation."""

    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_session", "session_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("learning_sessions.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    #: Provenance for the answer: provider, grounding state, citation list.
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[LearningSession] = relationship(back_populates="messages")
    citations: Mapped[list[Citation]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class Citation(Base):
    """A resolved pointer from generated text back into a source document."""

    __tablename__ = "citations"
    __table_args__ = (Index("ix_citations_message", "message_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    message_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("messages.id", ondelete="CASCADE")
    )
    chunk_id: Mapped[str] = mapped_column(String(32))
    document_id: Mapped[str] = mapped_column(String(32))
    page_number: Mapped[int] = mapped_column(Integer)
    marker: Mapped[int] = mapped_column(Integer, default=1)
    quote: Mapped[str] = mapped_column(Text, default="")
    bbox: Mapped[list[Any] | None] = mapped_column(JSON, default=None)
    score: Mapped[float] = mapped_column(Float, default=0.0)

    message: Mapped[Message] = relationship(back_populates="citations")


class Flashcard(Base, TimestampMixin):
    __tablename__ = "flashcards"
    __table_args__ = (Index("ix_flashcards_user", "user_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"))
    course_id: Mapped[str] = mapped_column(String(32), ForeignKey("courses.id", ondelete="CASCADE"))
    document_id: Mapped[str | None] = mapped_column(String(32), default=None)
    topic_slug: Mapped[str] = mapped_column(String(256), default="")
    front: Mapped[str] = mapped_column(Text)
    back: Mapped[str] = mapped_column(Text)
    difficulty: Mapped[str] = mapped_column(String(16), default="medium")
    #: Provenance is a first-class field: never claim a card came from a past paper.
    #: "ai_generated" | "past_paper" | "past_paper_variation" | "user"
    provenance: Mapped[str] = mapped_column(String(32), default="ai_generated")
    source_page: Mapped[int | None] = mapped_column(Integer, default=None)
    source_chunk_id: Mapped[str | None] = mapped_column(String(32), default=None)
    # SM-2 style scheduling state.
    ease: Mapped[float] = mapped_column(Float, default=2.5)
    interval_days: Mapped[int] = mapped_column(Integer, default=0)
    repetitions: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False)

    reviews: Mapped[list[FlashcardReview]] = relationship(
        back_populates="card", cascade="all, delete-orphan"
    )


class FlashcardReview(Base):
    __tablename__ = "flashcard_reviews"
    __table_args__ = (Index("ix_reviews_card", "card_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    card_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("flashcards.id", ondelete="CASCADE")
    )
    rating: Mapped[int] = mapped_column(Integer)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    interval_days: Mapped[int] = mapped_column(Integer, default=0)

    card: Mapped[Flashcard] = relationship(back_populates="reviews")


class Assessment(Base, TimestampMixin):
    """A generated quiz or mock examination."""

    __tablename__ = "assessments"
    __table_args__ = (Index("ix_assessments_user", "user_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"))
    course_id: Mapped[str] = mapped_column(String(32), ForeignKey("courses.id", ondelete="CASCADE"))
    document_id: Mapped[str | None] = mapped_column(String(32), default=None)
    #: "quiz" | "mock_exam"
    kind: Mapped[str] = mapped_column(String(16), default="quiz")
    title: Mapped[str] = mapped_column(String(256), default="")
    blueprint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    time_limit_seconds: Mapped[int | None] = mapped_column(Integer, default=None)
    total_marks: Mapped[int] = mapped_column(Integer, default=0)
    generator: Mapped[str] = mapped_column(String(64), default="")

    questions: Mapped[list[Question]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan", order_by="Question.ordinal"
    )
    attempts: Mapped[list[AssessmentAttempt]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan"
    )


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (Index("ix_questions_assessment", "assessment_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("assessments.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    #: "mcq" | "short_answer" | "numeric" | "explanation"
    kind: Mapped[str] = mapped_column(String(16), default="mcq")
    prompt: Mapped[str] = mapped_column(Text)
    options: Mapped[list[Any]] = mapped_column(JSON, default=list)
    correct_option: Mapped[int | None] = mapped_column(Integer, default=None)
    expected_answer: Mapped[str] = mapped_column(Text, default="")
    #: Keywords/criteria used for deterministic or rubric-based marking.
    rubric: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    numeric_answer: Mapped[float | None] = mapped_column(Float, default=None)
    numeric_tolerance: Mapped[float] = mapped_column(Float, default=0.01)
    marks: Mapped[int] = mapped_column(Integer, default=1)
    difficulty: Mapped[str] = mapped_column(String(16), default="medium")
    topic_slug: Mapped[str] = mapped_column(String(256), default="")
    provenance: Mapped[str] = mapped_column(String(32), default="ai_generated")
    source_chunk_id: Mapped[str | None] = mapped_column(String(32), default=None)
    source_page: Mapped[int | None] = mapped_column(Integer, default=None)
    explanation: Mapped[str] = mapped_column(Text, default="")

    assessment: Mapped[Assessment] = relationship(back_populates="questions")


class AssessmentAttempt(Base):
    __tablename__ = "assessment_attempts"
    __table_args__ = (Index("ix_attempts_assessment", "assessment_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("assessments.id", ondelete="CASCADE")
    )
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    max_score: Mapped[float] = mapped_column(Float, default=0.0)
    feedback: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    assessment: Mapped[Assessment] = relationship(back_populates="attempts")
    responses: Mapped[list[QuestionResponse]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )


class QuestionResponse(Base):
    __tablename__ = "question_responses"
    __table_args__ = (Index("ix_responses_attempt", "attempt_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    attempt_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("assessment_attempts.id", ondelete="CASCADE")
    )
    question_id: Mapped[str] = mapped_column(String(32))
    selected_option: Mapped[int | None] = mapped_column(Integer, default=None)
    answer_text: Mapped[str] = mapped_column(Text, default="")
    numeric_value: Mapped[float | None] = mapped_column(Float, default=None)
    awarded_marks: Mapped[float] = mapped_column(Float, default=0.0)
    max_marks: Mapped[float] = mapped_column(Float, default=0.0)
    correct: Mapped[bool | None] = mapped_column(Boolean, default=None)
    #: "deterministic" | "rubric" | "llm_rubric" | "unmarked"
    marking_method: Mapped[str] = mapped_column(String(24), default="deterministic")
    feedback: Mapped[str] = mapped_column(Text, default="")

    attempt: Mapped[AssessmentAttempt] = relationship(back_populates="responses")


class MasteryEstimate(Base, TimestampMixin):
    """Interpretable per-topic mastery with uncertainty and evidence count."""

    __tablename__ = "mastery_estimates"
    __table_args__ = (
        UniqueConstraint("user_id", "course_id", "topic_slug", name="uq_mastery_scope"),
        Index("ix_mastery_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"))
    course_id: Mapped[str] = mapped_column(String(32), ForeignKey("courses.id", ondelete="CASCADE"))
    topic_slug: Mapped[str] = mapped_column(String(256))
    topic_name: Mapped[str] = mapped_column(String(256), default="")
    #: P(knows the skill) under Bayesian Knowledge Tracing.
    probability: Mapped[float] = mapped_column(Float, default=0.25)
    observations: Mapped[int] = mapped_column(Integer, default=0)
    correct_observations: Mapped[int] = mapped_column(Integer, default=0)
    #: Width of the credible interval; wide means "we do not know yet".
    uncertainty: Mapped[float] = mapped_column(Float, default=1.0)
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Note(Base, TimestampMixin):
    __tablename__ = "notes"
    __table_args__ = (Index("ix_notes_user", "user_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"))
    course_id: Mapped[str] = mapped_column(String(32), ForeignKey("courses.id", ondelete="CASCADE"))
    document_id: Mapped[str | None] = mapped_column(String(32), default=None)
    title: Mapped[str] = mapped_column(String(256))
    #: "concept" | "exam" | "weakness"
    mode: Mapped[str] = mapped_column(String(16), default="concept")
    body_markdown: Mapped[str] = mapped_column(Text, default="")
    generated_by: Mapped[str] = mapped_column(String(64), default="")
    edited_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    citations: Mapped[list[Any]] = mapped_column(JSON, default=list)


class AttentionSample(Base):
    """Aggregated, opt-in attention estimates.

    Stores only element-level dwell aggregates, never raw gaze coordinates or
    camera frames. See docs/PRIVACY.md.
    """

    __tablename__ = "attention_samples"
    __table_args__ = (Index("ix_attention_user", "user_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"))
    document_id: Mapped[str] = mapped_column(String(32))
    page_number: Mapped[int] = mapped_column(Integer)
    element_index: Mapped[int] = mapped_column(Integer)
    dwell_ms: Mapped[int] = mapped_column(Integer, default=0)
    #: "simulated" | "webcam" — simulated is the MVP default.
    source: Mapped[str] = mapped_column(String(16), default="simulated")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


ALL_TABLES = Base.metadata
