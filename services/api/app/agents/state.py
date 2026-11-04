"""Typed LangGraph state.

Only what the graph needs to reason about is kept here.  Raw camera frames,
gaze streams, credentials and API keys are deliberately absent: the state is
checkpointed to disk, so anything in it is persisted.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, NotRequired, TypedDict

LearningMode = Literal["read", "explain", "question", "assignment", "assess"]


def _replace(_current: Any, incoming: Any) -> Any:
    """Reducer: later writes replace earlier ones."""
    return incoming


def _extend(current: list[Any] | None, incoming: list[Any] | None) -> list[Any]:
    """Reducer: accumulate across nodes (used for warnings)."""
    return list(current or []) + list(incoming or [])


class TutorState(TypedDict):
    """The state threaded through the learning graph."""

    # --- identity and scope ---------------------------------------------
    session_id: str
    user_id: str
    course_id: str
    document_id: NotRequired[str]

    # --- where the student is -------------------------------------------
    current_page: int
    current_topic: str
    learning_mode: LearningMode

    # --- the turn --------------------------------------------------------
    student_question: str
    explanation_level: NotRequired[str]
    #: Guidance policy for assignment mode: socratic | explain | off
    assignment_policy: NotRequired[str]
    #: Hint level already reached in a Socratic exchange (1..6).
    hint_level: NotRequired[int]

    # --- retrieval -------------------------------------------------------
    retrieved_sources: Annotated[list[dict[str, Any]], _replace]
    query_class: NotRequired[str]
    evidence_sufficient: NotRequired[bool]
    evidence_note: NotRequired[str]
    reformulated_query: NotRequired[str | None]

    # --- generation ------------------------------------------------------
    answer: NotRequired[str]
    citations: Annotated[list[dict[str, Any]], _replace]
    #: True when the answer is supported by retrieved course material.
    grounded: NotRequired[bool]
    #: True when a language model wrote the answer; False for source quoting.
    generative: NotRequired[bool]
    provider: NotRequired[str]
    model: NotRequired[str]
    input_tokens: NotRequired[int | None]
    output_tokens: NotRequired[int | None]
    #: True when the graph declined to answer rather than guessing.
    abstained: NotRequired[bool]

    # --- learning state ---------------------------------------------------
    quiz_results: NotRequired[dict[str, Any]]
    mastery_state: NotRequired[dict[str, Any]]
    recommended_action: NotRequired[str]
    recommendation_reason: NotRequired[str]

    warnings: Annotated[list[str], _extend]


def new_state(
    *,
    session_id: str,
    user_id: str,
    course_id: str,
    student_question: str,
    document_id: str = "",
    current_page: int = 1,
    current_topic: str = "",
    learning_mode: LearningMode = "question",
    explanation_level: str = "intermediate",
    assignment_policy: str = "socratic",
    hint_level: int = 0,
) -> TutorState:
    return TutorState(
        session_id=session_id,
        user_id=user_id,
        course_id=course_id,
        document_id=document_id,
        current_page=current_page,
        current_topic=current_topic,
        learning_mode=learning_mode,
        student_question=student_question,
        explanation_level=explanation_level,
        assignment_policy=assignment_policy,
        hint_level=hint_level,
        retrieved_sources=[],
        citations=[],
        warnings=[],
    )
