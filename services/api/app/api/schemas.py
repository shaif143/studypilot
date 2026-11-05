"""Pydantic request and response models.

These are the API contract shared with the TypeScript client; field names match
the generated types in ``packages/api-client``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ORM = ConfigDict(from_attributes=True)


# --- health ---------------------------------------------------------------
class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    runtime: dict[str, Any]


# --- providers / settings -------------------------------------------------
class ProviderInfoOut(BaseModel):
    key: str
    label: str
    sends_data_off_device: bool
    available: bool
    generative: bool
    requires_api_key: bool
    detail: str
    model: str
    #: Which non-text briefs this provider can read. Decides whether an
    #: image or audio upload can be transcribed on this installation.
    accepts_images: bool = False
    accepts_audio: bool = False


class ProvidersResponse(BaseModel):
    llm: list[ProviderInfoOut]
    embedding: list[ProviderInfoOut]
    active_llm: str
    active_embedding: str
    #: True when the active generation provider actually writes text.
    active_is_generative: bool


class SettingsOut(BaseModel):
    model_config = ORM

    theme: str
    preferred_llm_provider: str = ""
    learning_style: str
    explanation_level: str
    assignment_policy: str
    quiz_checkpoint_slides: int
    allow_cloud_processing: bool
    allow_cloud_vision: bool
    allow_external_search: bool
    allow_telemetry: bool
    allow_attention_tracking: bool
    allow_federated_participation: bool
    retention_days: int | None


class SettingsUpdate(BaseModel):
    theme: Literal["system", "light", "dark"] | None = None
    #: "" clears the choice and returns to the installation default.
    preferred_llm_provider: Literal["", "extractive", "ollama", "anthropic", "gemini"] | None = None
    learning_style: str | None = None
    explanation_level: Literal["simple", "intermediate", "advanced"] | None = None
    assignment_policy: Literal["socratic", "explain", "off"] | None = None
    quiz_checkpoint_slides: int | None = Field(default=None, ge=1, le=100)
    allow_cloud_processing: bool | None = None
    allow_cloud_vision: bool | None = None
    allow_external_search: bool | None = None
    allow_telemetry: bool | None = None
    allow_attention_tracking: bool | None = None
    allow_federated_participation: bool | None = None
    retention_days: int | None = Field(default=None, ge=1, le=3650)


# --- courses --------------------------------------------------------------
class CourseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    code: str = Field(default="", max_length=64)
    description: str = Field(default="", max_length=4000)
    institution: str = Field(default="", max_length=256)
    integrity_policy: str = Field(default="", max_length=4000)


class CourseUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=256)
    code: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=4000)
    institution: str | None = Field(default=None, max_length=256)
    integrity_policy: str | None = Field(default=None, max_length=4000)
    archived: bool | None = None


class CourseOut(BaseModel):
    model_config = ORM

    id: str
    title: str
    code: str
    description: str
    institution: str
    integrity_policy: str
    archived: bool
    created_at: datetime
    document_count: int = 0
    page_count: int = 0
    topic_count: int = 0


# --- documents ------------------------------------------------------------
class DocumentOut(BaseModel):
    model_config = ORM

    id: str
    course_id: str
    title: str
    original_filename: str
    media_type: str
    byte_size: int
    page_count: int
    status: str
    status_detail: str
    kind: str
    embedding_model: str
    extraction_meta: dict[str, Any]
    created_at: datetime


class PageElementOut(BaseModel):
    index: int
    kind: str
    text: str
    bbox: list[float]
    font_size: float = 0.0
    bold: bool = False


class PageOut(BaseModel):
    model_config = ORM

    page_number: int
    heading: str
    text: str
    width: float
    height: float
    preview_path: str
    has_equations: bool
    has_figures: bool
    has_tables: bool
    elements: list[PageElementOut] = Field(default_factory=list)


class PageSummaryOut(BaseModel):
    model_config = ORM

    page_number: int
    heading: str
    preview_path: str
    has_equations: bool
    has_figures: bool
    has_tables: bool


class UploadResponse(BaseModel):
    document: DocumentOut
    status: str
    page_count: int
    chunk_count: int
    topic_count: int
    warnings: list[str] = Field(default_factory=list)
    duration_ms: int
    #: True when an identical file was already in this course.
    deduplicated: bool = False


class TopicOut(BaseModel):
    model_config = ORM

    id: str
    name: str
    slug: str
    summary: str
    document_id: str | None
    first_page: int
    last_page: int


# --- tutor ----------------------------------------------------------------
class CitationOut(BaseModel):
    marker: int
    chunk_id: str
    document_id: str
    document_title: str = ""
    page_number: int
    section: str = ""
    bbox: list[float] | None = None
    score: float = 0.0
    quote: str = ""
    cited_inline: bool = True


class SourceOut(BaseModel):
    chunk_id: str
    text: str
    page_number: int
    document_id: str
    document_title: str
    section: str
    topic_slug: str
    element_type: str
    bbox: list[float] | None = None
    score: float
    retrievers: list[str] = Field(default_factory=list)


class AskRequest(BaseModel):
    question: str = Field(default="", max_length=4000)
    course_id: str | None = None
    document_id: str | None = None
    session_id: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    mode: Literal["question", "explain", "assignment"] = "question"
    explanation_level: Literal["simple", "intermediate", "advanced"] | None = None
    hint_level: int = Field(default=1, ge=1, le=6)
    #: Per-request opt-in to a cloud provider. Required in addition to the
    #: stored setting; one does not imply the other.
    allow_cloud: bool = False
    provider: str | None = None

    @field_validator("question")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class AnswerResponse(BaseModel):
    session_id: str
    message_id: str
    answer: str
    citations: list[CitationOut] = Field(default_factory=list)
    sources: list[SourceOut] = Field(default_factory=list)
    #: Supported by retrieved course material with at least one valid citation.
    grounded: bool
    #: False when the text was quoted from sources rather than written by a model.
    generative: bool
    abstained: bool
    provider: str
    model: str
    query_class: str = ""
    evidence_sufficient: bool = True
    evidence_note: str = ""
    recommended_action: str = ""
    recommendation_reason: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    warnings: list[str] = Field(default_factory=list)


class MessageOut(BaseModel):
    model_config = ORM

    id: str
    role: str
    content: str
    created_at: datetime
    meta: dict[str, Any] = Field(default_factory=dict)
    citations: list[CitationOut] = Field(default_factory=list)


class LearningSessionOut(BaseModel):
    model_config = ORM

    id: str
    course_id: str | None
    document_id: str | None
    current_page: int
    current_topic: str
    learning_mode: str
    recommended_action: str
    slides_since_checkpoint: int
    last_active_at: datetime
    document_title: str = ""
    course_title: str = ""


class SessionUpdate(BaseModel):
    current_page: int | None = Field(default=None, ge=1)
    current_topic: str | None = None
    learning_mode: str | None = None
    slides_since_checkpoint: int | None = Field(default=None, ge=0)


# --- assessment -----------------------------------------------------------
class QuizGenerateRequest(BaseModel):
    course_id: str
    document_id: str | None = None
    topic_slug: str | None = None
    page_from: int | None = Field(default=None, ge=1)
    page_to: int | None = Field(default=None, ge=1)
    question_count: int = Field(default=5, ge=1, le=40)
    kinds: list[Literal["mcq", "short_answer", "numeric", "explanation"]] = Field(
        default_factory=lambda: ["mcq", "short_answer"]
    )
    difficulty: Literal["easy", "balanced", "hard"] = "balanced"
    kind: Literal["quiz", "mock_exam"] = "quiz"
    time_limit_seconds: int | None = Field(default=None, ge=60, le=4 * 3600)
    topic_weights: dict[str, float] | None = None
    allow_cloud: bool = False


class QuestionOut(BaseModel):
    model_config = ORM

    id: str
    ordinal: int
    kind: str
    prompt: str
    options: list[str] = Field(default_factory=list)
    marks: int
    difficulty: str
    topic_slug: str
    provenance: str
    source_page: int | None = None


class AssessmentOut(BaseModel):
    model_config = ORM

    id: str
    course_id: str
    document_id: str | None
    kind: str
    title: str
    total_marks: int
    time_limit_seconds: int | None
    generator: str
    created_at: datetime
    questions: list[QuestionOut] = Field(default_factory=list)
    blueprint: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ResponseIn(BaseModel):
    question_id: str
    selected_option: int | None = None
    answer_text: str = Field(default="", max_length=8000)
    numeric_value: float | None = None


class SubmitRequest(BaseModel):
    responses: list[ResponseIn]
    allow_cloud: bool = False


class GradedResponseOut(BaseModel):
    question_id: str
    awarded_marks: float
    max_marks: float
    correct: bool | None
    marking_method: str
    feedback: str
    expected_answer: str = ""
    correct_option: int | None = None
    explanation: str = ""
    source_page: int | None = None


class AttemptResultOut(BaseModel):
    attempt_id: str
    assessment_id: str
    score: float
    max_score: float
    percentage: float
    responses: list[GradedResponseOut]
    weak_topics: list[str] = Field(default_factory=list)
    mastery_changes: list[dict[str, Any]] = Field(default_factory=list)
    recommended_action: str = ""
    recommendation_reason: str = ""


# --- flashcards -----------------------------------------------------------
class FlashcardGenerateRequest(BaseModel):
    course_id: str
    document_id: str | None = None
    topic_slug: str | None = None
    page_from: int | None = Field(default=None, ge=1)
    page_to: int | None = Field(default=None, ge=1)
    count: int = Field(default=10, ge=1, le=60)
    allow_cloud: bool = False


class FlashcardOut(BaseModel):
    model_config = ORM

    id: str
    course_id: str
    document_id: str | None
    topic_slug: str
    front: str
    back: str
    difficulty: str
    provenance: str
    source_page: int | None
    ease: float
    interval_days: int
    repetitions: int
    due_at: datetime


class FlashcardReviewIn(BaseModel):
    rating: int = Field(ge=0, le=3)


# --- notes ----------------------------------------------------------------
class NoteGenerateRequest(BaseModel):
    course_id: str
    document_id: str | None = None
    mode: Literal["concept", "exam", "weakness"] = "concept"
    topic_slug: str | None = None
    title: str | None = None
    allow_cloud: bool = False


class NoteOut(BaseModel):
    model_config = ORM

    id: str
    course_id: str
    document_id: str | None
    title: str
    mode: str
    body_markdown: str
    generated_by: str
    edited_by_user: bool
    citations: list[Any] = Field(default_factory=list)
    updated_at: datetime


class NoteUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    body_markdown: str | None = Field(default=None, max_length=200_000)


# --- analytics ------------------------------------------------------------
class MasteryOut(BaseModel):
    model_config = ORM

    topic_slug: str
    topic_name: str
    probability: float
    observations: int
    correct_observations: int
    uncertainty: float
    label: str = "unknown"
    confident: bool = False


class RecommendationOut(BaseModel):
    action: str
    topic_slug: str
    topic_name: str
    reason: str
    priority: int


class DashboardResponse(BaseModel):
    course_count: int
    document_count: int
    page_count: int
    indexed_chunk_count: int
    due_flashcards: int
    upcoming_reviews: int
    recent_sessions: list[LearningSessionOut] = Field(default_factory=list)
    recent_documents: list[DocumentOut] = Field(default_factory=list)
    mastery: list[MasteryOut] = Field(default_factory=list)
    weak_topics: list[MasteryOut] = Field(default_factory=list)
    recommendations: list[RecommendationOut] = Field(default_factory=list)
    recent_attempts: list[dict[str, Any]] = Field(default_factory=list)


# --- attention (experimental) ---------------------------------------------
class AttentionSampleIn(BaseModel):
    page_number: int = Field(ge=1)
    element_index: int = Field(ge=0)
    dwell_ms: int = Field(ge=0, le=600_000)
    source: Literal["simulated", "webcam"] = "simulated"


class AttentionBatchIn(BaseModel):
    document_id: str
    samples: list[AttentionSampleIn] = Field(max_length=2000)


class AttentionReportOut(BaseModel):
    document_id: str
    #: Elements with little recorded dwell relative to the page average.
    low_attention: list[dict[str, Any]] = Field(default_factory=list)
    totals: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool
    note: str


# --- privacy --------------------------------------------------------------
class ExportResponse(BaseModel):
    exported_at: datetime
    profile: dict[str, Any]
    courses: list[dict[str, Any]]
    documents: list[dict[str, Any]]
    sessions: list[dict[str, Any]]
    flashcards: list[dict[str, Any]]
    assessments: list[dict[str, Any]]
    notes: list[dict[str, Any]]
    mastery: list[dict[str, Any]]
    note: str


class DeleteResultOut(BaseModel):
    deleted: bool
    detail: str


# --- assignment guides ----------------------------------------------------
class PlanCitationOut(BaseModel):
    marker: int
    chunk_id: str
    document_id: str
    document_title: str = ""
    page_number: int
    section: str = ""
    quote: str = ""


class PlanStepOut(BaseModel):
    index: int
    title: str
    instruction: str
    why: str = ""
    requirement_ids: list[str] = Field(default_factory=list)
    #: "slides" when grounded in the course's lecture material, else "beyond".
    grounding: Literal["slides", "beyond"]
    citations: list[PlanCitationOut] = Field(default_factory=list)
    approval: Literal["not_required", "pending", "approved", "declined"]
    #: The warning shown on a step that goes beyond the slides. Empty when grounded.
    disclosure: str = ""
    coverage: float = 0.0
    model_flagged_beyond: bool = False
    beyond_reason: str = ""


class RequirementOut(BaseModel):
    id: str
    text: str
    kind: Literal["deliverable", "constraint", "criterion", "tool"] = "deliverable"
    quote: str = ""


class AssignmentPlanOut(BaseModel):
    model_config = ORM

    id: str
    course_id: str
    document_id: str
    status: Literal["ready", "needs_approval", "failed"]
    summary: str
    requirements: list[RequirementOut]
    steps: list[PlanStepOut]
    provider: str
    model: str
    #: False means no language model drafted this: it is the brief's
    #: requirements with matching passages, and the UI must say so.
    generative: bool
    warnings: list[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    created_at: datetime


class PlanRequest(BaseModel):
    #: Per-request opt-in to a cloud provider, in addition to the stored setting.
    allow_cloud: bool = False
    provider: str | None = None


class StepApprovalRequest(BaseModel):
    approval: Literal["approved", "declined"]


class AssignmentOut(BaseModel):
    """One assignment brief in a course, with its latest plan if any."""

    document: DocumentOut
    plan: AssignmentPlanOut | None = None


# --- provider keys (desktop) ------------------------------------------------
class ProviderKeyStatusOut(BaseModel):
    provider: str
    configured: bool
    #: Set outside the app (environment); cannot be removed from inside it.
    from_environment: bool


class ProviderKeysOut(BaseModel):
    #: False on a hosted deployment, where keys belong to the operator.
    editable: bool
    keys: list[ProviderKeyStatusOut]


class ProviderKeyIn(BaseModel):
    key: str = Field(min_length=8, max_length=512)
