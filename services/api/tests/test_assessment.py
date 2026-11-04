"""Question generation and marking."""

from __future__ import annotations

import pytest

from app.assessment.generator import (
    QuestionGenerator,
    _is_fair_distractor,
    build_blueprint,
    reflow,
)
from app.assessment.grading import grade_response
from app.rag.retriever import RetrievedChunk
from app.storage.models import Question


def chunk(text: str, page: int = 1, **kwargs) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=kwargs.pop("chunk_id", f"chunk-{page}"),
        text=text,
        page_number=page,
        document_id="doc",
        document_title="Lecture",
        section=kwargs.pop("section", "Padding"),
        topic_slug=kwargs.pop("topic_slug", "padding"),
        element_type="text",
        bbox=None,
        score=1.0,
        parent_text=text,
    )


# --- text preparation -----------------------------------------------------
def test_reflow_rejoins_wrapped_lines():
    """A PDF wraps mid-sentence; those line breaks are not sentence ends."""
    wrapped = (
        "Padding is necessary for two reasons. First, without padding the output\n"
        "is smaller than the input, so a deep stack of convolutions shrinks the\n"
        "feature map."
    )
    result = reflow(wrapped)
    # Reflow repairs the wrap points only; splitting into sentences happens
    # afterwards, on the punctuation this leaves intact.
    assert "the output is smaller than the input" in result
    assert "shrinks the feature map" in result
    assert "\n" not in result
    assert result.count(".") == 2


def test_reflow_keeps_separate_bullets_apart():
    bullets = (
        "Convolution gives weight sharing\n"
        "Padding preserves spatial size\n"
        "Stride reduces resolution"
    )
    assert reflow(bullets).count("\n") == 2


def test_reflow_handles_empty_input():
    assert reflow("") == ""
    assert reflow("\n\n  \n") == ""


def test_distractors_reject_morphological_variants():
    """ "convolution" against "convolutional" tests spelling, not understanding."""
    assert _is_fair_distractor("pooling", "convolution") is True
    assert _is_fair_distractor("convolution", "convolutional") is False
    assert _is_fair_distractor("convolutional", "convolution") is False
    assert _is_fair_distractor("Padding", "padding") is False
    # Wildly different lengths give the answer away by shape.
    assert _is_fair_distractor("backpropagation", "stride") is False


# --- deterministic generation --------------------------------------------
def test_source_derived_questions_are_grounded_and_well_formed():
    chunks = [
        chunk(
            "Padding adds a border of extra values, usually zeros, around the input\n"
            "before the kernel is applied.\n"
            "Slide 4",
            page=4,
        ),
        chunk(
            "Stride is the step size the kernel moves between applications.\n"
            "A stride of one moves the kernel by a single position.\n"
            "Slide 5",
            page=5,
            chunk_id="chunk-5",
            topic_slug="stride",
        ),
    ]
    questions = QuestionGenerator(seed=1).from_source(
        chunks, blueprint=build_blueprint(question_count=2, kinds=["mcq", "short_answer"])
    )

    assert len(questions) == 2
    for question in questions:
        # Provenance must be honest, and traceable to a real passage.
        assert question.provenance == "source_derived"
        assert question.source_chunk_id in {c.chunk_id for c in chunks}
        assert question.source_page in {4, 5}
        assert "______" in question.prompt
        # Page furniture must never appear in a question.
        assert "Slide 4" not in question.prompt
        assert "Slide 5" not in question.prompt
        if question.kind == "mcq":
            assert question.correct_option is not None
            assert question.options[question.correct_option] == question.expected_answer
            assert len(set(question.options)) == len(question.options)


def test_generation_is_deterministic_for_a_seed():
    chunks = [
        chunk(
            "Pooling reduces the spatial size of a feature map by summarising small "
            "neighbourhoods. Max pooling takes the largest value in each window.",
            page=6,
        )
    ]
    blueprint = build_blueprint(question_count=1, kinds=["mcq"])
    first = QuestionGenerator(seed=42).from_source(chunks, blueprint=blueprint)
    second = QuestionGenerator(seed=42).from_source(chunks, blueprint=blueprint)
    assert [q.prompt for q in first] == [q.prompt for q in second]
    assert [q.options for q in first] == [q.options for q in second]


def test_generation_yields_nothing_from_unusable_text():
    """Too short to carry a question: produce nothing rather than nonsense."""
    assert (
        QuestionGenerator().from_source(
            [chunk("Slide 1", page=1)], blueprint=build_blueprint(question_count=3)
        )
        == []
    )


def test_blueprint_clamps_and_validates():
    blueprint = build_blueprint(question_count=999, kinds=["mcq", "essay"])  # type: ignore[list-item]
    assert blueprint["question_count"] == 60
    assert blueprint["kinds"] == ["mcq"]
    assert build_blueprint(question_count=0)["question_count"] == 1


# --- marking ---------------------------------------------------------------
def make_question(**kwargs) -> Question:
    defaults = {
        "assessment_id": "a",
        "ordinal": 0,
        "kind": "mcq",
        "prompt": "What does padding add?",
        "options": ["A border", "A kernel", "A stride"],
        "correct_option": 0,
        "expected_answer": "A border",
        "rubric": {},
        "marks": 1,
        "difficulty": "easy",
        "topic_slug": "padding",
        "provenance": "source_derived",
        "explanation": "Page 4 says padding adds a border.",
        "numeric_tolerance": 0.01,
    }
    defaults.update(kwargs)
    return Question(**defaults)


def test_mcq_marking_is_exact():
    question = make_question()
    right = grade_response(question, selected_option=0)
    assert (right.correct, right.awarded_marks, right.credit) == (True, 1.0, 1.0)
    assert right.marking_method == "deterministic"

    wrong = grade_response(question, selected_option=2)
    assert wrong.correct is False
    assert "A border" in wrong.feedback
    assert "padding adds a border" in wrong.feedback

    blank = grade_response(question, selected_option=None)
    assert blank.correct is False
    assert "No option selected" in blank.feedback


def test_numeric_marking_uses_tolerance():
    question = make_question(
        kind="numeric", numeric_answer=32.0, numeric_tolerance=0.05, marks=2, options=[]
    )
    assert grade_response(question, numeric_value=32.0).correct is True
    assert grade_response(question, numeric_value=33.0).correct is True  # within 5%
    assert grade_response(question, numeric_value=40.0).correct is False
    # A number is read out of free text when the field was not used.
    assert grade_response(question, answer_text="I get about 32").correct is True
    unreadable = grade_response(question, answer_text="no idea")
    assert unreadable.marking_method == "unmarked"
    assert unreadable.correct is None


def test_numeric_tolerance_is_absolute_near_zero():
    question = make_question(kind="numeric", numeric_answer=0.0, numeric_tolerance=0.01, options=[])
    assert grade_response(question, numeric_value=0.005).correct is True
    assert grade_response(question, numeric_value=0.5).correct is False


def test_written_answers_get_partial_credit():
    question = make_question(
        kind="short_answer",
        options=[],
        correct_option=None,
        marks=4,
        rubric={"method": "rubric", "points": ["edge pixels", "spatial size", "zeros"]},
    )
    full = grade_response(
        question, answer_text="It preserves spatial size, pads zeros, and treats edge pixels fairly"
    )
    assert full.credit == pytest.approx(1.0)
    assert full.awarded_marks == 4.0
    assert full.correct is True

    partial = grade_response(question, answer_text="It keeps the spatial size the same")
    assert 0 < partial.credit < 1
    assert partial.correct is None  # neither right nor wrong
    assert "Not covered" in partial.feedback
    assert partial.marking_method == "rubric"

    none = grade_response(question, answer_text="I am not sure about this one at all")
    assert none.credit == 0.0
    assert none.correct is False


def test_rubric_matching_tolerates_inflection():
    question = make_question(
        kind="short_answer",
        options=[],
        correct_option=None,
        rubric={"method": "keyword", "points": ["convolution"]},
    )
    assert grade_response(question, answer_text="we use convolutions here").credit == 1.0


def test_unmarkable_written_answer_says_so():
    question = make_question(
        kind="explanation", options=[], correct_option=None, expected_answer="", rubric={}
    )
    result = grade_response(
        question, answer_text="A long answer with no rubric to mark it against."
    )
    assert result.marking_method == "unmarked"
    assert result.correct is None
    assert "no marking rubric" in result.feedback
