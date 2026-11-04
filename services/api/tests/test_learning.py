"""Knowledge tracing, scheduling and recommendations."""

from __future__ import annotations

import pytest

from app.learning.mastery import (
    MIN_OBSERVATIONS_FOR_CONFIDENCE,
    recommend_action,
    schedule_review,
    update_mastery,
)
from app.storage.models import MasteryEstimate


def _estimate(probability: float = 0.25, observations: int = 0, name: str = "Padding"):
    return MasteryEstimate(
        user_id="u",
        course_id="c",
        topic_slug=name.lower(),
        topic_name=name,
        probability=probability,
        observations=observations,
        correct_observations=0,
        uncertainty=1.0,
    )


def test_one_wrong_answer_does_not_collapse_mastery():
    """A single mistake must not be treated as proof of not understanding."""
    estimate = _estimate(probability=0.8, observations=5)
    update = update_mastery(estimate, correct=False, question_kind="mcq")
    assert update.probability < 0.8
    # Still well above the weakness threshold after one slip.
    assert update.probability > 0.45


def test_mastery_is_not_claimed_without_evidence():
    estimate = _estimate()
    update = update_mastery(estimate, correct=True, question_kind="mcq")
    assert update.confident is False
    assert update.label == "unknown"
    assert update.uncertainty > 0.5


def test_repeated_correct_answers_build_confidence():
    estimate = _estimate()
    for _ in range(MIN_OBSERVATIONS_FOR_CONFIDENCE + 3):
        update = update_mastery(estimate, correct=True, question_kind="short_answer")
    assert update.confident is True
    assert update.label == "strong"
    assert update.probability > 0.85


def test_uncertainty_shrinks_monotonically():
    estimate = _estimate()
    previous = 1.1
    for _ in range(8):
        update = update_mastery(estimate, correct=True)
        assert update.uncertainty < previous
        previous = update.uncertainty


def test_mcq_correct_moves_less_than_written_correct():
    """Guessing is likelier on an MCQ, so it is weaker evidence."""
    mcq = _estimate()
    written = _estimate()
    mcq_update = update_mastery(mcq, correct=True, question_kind="mcq")
    written_update = update_mastery(written, correct=True, question_kind="short_answer")
    assert written_update.probability > mcq_update.probability


def test_partial_credit_lands_between_right_and_wrong():
    right = update_mastery(
        _estimate(), correct=True, question_kind="explanation", partial_credit=1.0
    )
    half = update_mastery(
        _estimate(), correct=False, question_kind="explanation", partial_credit=0.5
    )
    wrong = update_mastery(
        _estimate(), correct=False, question_kind="explanation", partial_credit=0.0
    )
    assert wrong.probability < half.probability < right.probability


def test_recommendation_prefers_a_weak_prerequisite():
    padding = _estimate(probability=0.3, observations=5, name="Padding")
    convolution = _estimate(probability=0.2, observations=5, name="Convolution")
    items = recommend_action(
        [padding, convolution],
        prerequisites={"padding": ["convolution"]},
    )
    actions = {item.action for item in items}
    assert "revisit_prerequisite" in actions
    prerequisite = next(item for item in items if item.action == "revisit_prerequisite")
    assert prerequisite.topic_name == "Convolution"
    assert "builds on" in prerequisite.reason


def test_due_flashcards_are_recommended_first():
    items = recommend_action([_estimate(probability=0.9, observations=5)], due_flashcards=4)
    assert items[0].action == "review_flashcards"
    assert "4 cards are due" in items[0].reason


def test_unseen_topic_is_recommended_for_a_checkpoint():
    items = recommend_action([_estimate(observations=0)])
    assert items[0].action == "checkpoint_quiz"


def test_schedule_review_lapse_resets_interval_but_keeps_most_ease():
    ease, interval, repetitions, _due = schedule_review(
        ease=2.5, interval_days=20, repetitions=5, rating=0
    )
    assert interval == 0
    assert repetitions == 0
    assert ease == pytest.approx(2.3)


def test_schedule_review_grows_intervals():
    ease, interval, repetitions, _ = schedule_review(
        ease=2.5, interval_days=0, repetitions=0, rating=2
    )
    assert (interval, repetitions) == (1, 1)
    ease, interval, repetitions, _ = schedule_review(
        ease=ease, interval_days=interval, repetitions=repetitions, rating=2
    )
    assert interval == 3
    ease, interval, repetitions, _ = schedule_review(
        ease=ease, interval_days=interval, repetitions=repetitions, rating=3
    )
    assert interval > 3
    assert interval <= 365


def test_schedule_review_caps_interval():
    _, interval, _, _ = schedule_review(ease=3.0, interval_days=300, repetitions=20, rating=3)
    assert interval == 365
