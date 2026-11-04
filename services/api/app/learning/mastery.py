"""Interpretable student knowledge model.

Bayesian Knowledge Tracing is used rather than a neural knowledge tracer
because every number it produces can be explained to the student, and because
it behaves sensibly with the handful of observations a real study session
produces.  Deep knowledge tracing can be added behind the same functions later.

Two deliberate design points:

* A single wrong answer never collapses mastery.  BKT's guess/slip parameters
  mean one observation moves the posterior modestly, and ``uncertainty`` stays
  wide until several observations exist.  The UI shows "not enough evidence yet"
  rather than a confident low score.
* Recommendations are rule-based and deterministic.  Scheduling and progression
  are high-stakes for a student's time, so they are not left to a model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.storage.models import MasteryEstimate

#: Below this, a topic is shown as a weakness.
WEAK_THRESHOLD = 0.6
#: At or above this, with enough evidence, a topic counts as mastered.
MASTERY_THRESHOLD = 0.85
#: Fewer observations than this and we say so instead of claiming a level.
MIN_OBSERVATIONS_FOR_CONFIDENCE = 3


@dataclass(frozen=True)
class BKTParameters:
    """Standard BKT parameters.

    ``p_guess``   probability of a correct answer without knowing the skill
    ``p_slip``    probability of a wrong answer while knowing the skill
    ``p_learn``   probability of learning the skill from one practice attempt
    ``p_forget``  probability of losing the skill between attempts
    """

    p_init: float = 0.25
    p_guess: float = 0.20
    p_slip: float = 0.12
    p_learn: float = 0.18
    p_forget: float = 0.02


#: Guessing is much likelier on a 4-option MCQ than on a written answer.
PARAMETERS_BY_QUESTION: dict[str, BKTParameters] = {
    "mcq": BKTParameters(p_guess=0.28, p_slip=0.10),
    "short_answer": BKTParameters(p_guess=0.10, p_slip=0.15),
    "numeric": BKTParameters(p_guess=0.05, p_slip=0.18),
    "explanation": BKTParameters(p_guess=0.08, p_slip=0.20),
    "flashcard": BKTParameters(p_guess=0.15, p_slip=0.15),
}


@dataclass
class MasteryUpdate:
    probability: float
    previous_probability: float
    observations: int
    uncertainty: float
    confident: bool
    label: str  # unknown | weak | developing | strong


def _posterior(prior: float, correct: bool, params: BKTParameters) -> float:
    """P(knows | observation) by Bayes, then the learning transition."""
    if correct:
        likelihood = prior * (1 - params.p_slip)
        evidence = likelihood + (1 - prior) * params.p_guess
    else:
        likelihood = prior * params.p_slip
        evidence = likelihood + (1 - prior) * (1 - params.p_guess)
    conditional = likelihood / evidence if evidence > 0 else prior
    # Practice itself teaches; knowledge also decays slightly.
    learned = conditional + (1 - conditional) * params.p_learn
    return min(0.999, max(0.001, learned * (1 - params.p_forget)))


def _uncertainty(observations: int) -> float:
    """Shrinking credible-interval width as evidence accumulates.

    Returns ~1.0 with no evidence and approaches 0 slowly, which is what keeps
    the UI honest about small samples.
    """
    return 1.0 / math.sqrt(1 + observations)


def _label(probability: float, observations: int) -> str:
    if observations < MIN_OBSERVATIONS_FOR_CONFIDENCE:
        return "unknown"
    if probability < WEAK_THRESHOLD:
        return "weak"
    if probability < MASTERY_THRESHOLD:
        return "developing"
    return "strong"


def update_mastery(
    estimate: MasteryEstimate,
    *,
    correct: bool,
    question_kind: str = "mcq",
    partial_credit: float | None = None,
) -> MasteryUpdate:
    """Apply one observation to a mastery estimate, mutating it in place.

    ``partial_credit`` (0..1) from a rubric-marked answer is treated as a
    weighted blend of a correct and an incorrect observation rather than being
    forced to a binary, so a half-right explanation is not scored as a failure.
    """
    params = PARAMETERS_BY_QUESTION.get(question_kind, BKTParameters())
    prior = float(estimate.probability)

    if partial_credit is None:
        posterior = _posterior(prior, correct, params)
    else:
        weight = min(1.0, max(0.0, partial_credit))
        posterior = weight * _posterior(prior, True, params) + (1 - weight) * _posterior(
            prior, False, params
        )

    estimate.probability = posterior
    estimate.observations += 1
    if correct or (partial_credit or 0) >= 0.5:
        estimate.correct_observations += 1
    estimate.uncertainty = _uncertainty(estimate.observations)
    estimate.last_observed_at = datetime.now(UTC)

    return MasteryUpdate(
        probability=posterior,
        previous_probability=prior,
        observations=estimate.observations,
        uncertainty=estimate.uncertainty,
        confident=estimate.observations >= MIN_OBSERVATIONS_FOR_CONFIDENCE,
        label=_label(posterior, estimate.observations),
    )


@dataclass
class Recommendation:
    action: str
    topic_slug: str
    topic_name: str
    reason: str
    priority: int


def recommend_action(
    estimates: list[MasteryEstimate],
    *,
    prerequisites: dict[str, list[str]] | None = None,
    due_flashcards: int = 0,
) -> list[Recommendation]:
    """Produce ranked, explainable next steps.

    Rule-based on purpose: the ordering is auditable, and the reason shown to
    the student is the actual rule that fired.
    """
    prerequisites = prerequisites or {}
    by_slug = {estimate.topic_slug: estimate for estimate in estimates}
    recommendations: list[Recommendation] = []

    if due_flashcards > 0:
        recommendations.append(
            Recommendation(
                action="review_flashcards",
                topic_slug="",
                topic_name="",
                reason=(
                    f"{due_flashcards} card{'s' if due_flashcards != 1 else ''} are due for review."
                ),
                priority=10,
            )
        )

    for estimate in estimates:
        label = _label(float(estimate.probability), estimate.observations)

        # A weak topic whose prerequisite is also weak: fix the prerequisite first.
        weak_prerequisite = next(
            (
                by_slug[slug]
                for slug in prerequisites.get(estimate.topic_slug, [])
                if slug in by_slug and float(by_slug[slug].probability) < WEAK_THRESHOLD
            ),
            None,
        )
        if label == "weak" and weak_prerequisite is not None:
            recommendations.append(
                Recommendation(
                    action="revisit_prerequisite",
                    topic_slug=weak_prerequisite.topic_slug,
                    topic_name=weak_prerequisite.topic_name,
                    reason=(
                        f"{estimate.topic_name} builds on {weak_prerequisite.topic_name}, "
                        "which is not solid yet."
                    ),
                    priority=30,
                )
            )
            continue

        if label == "weak":
            recommendations.append(
                Recommendation(
                    action="targeted_explanation",
                    topic_slug=estimate.topic_slug,
                    topic_name=estimate.topic_name,
                    reason=(
                        f"{estimate.correct_observations} of "
                        f"{estimate.observations} questions "
                        f"on {estimate.topic_name} were right."
                    ),
                    priority=25,
                )
            )
        elif label == "unknown" and estimate.observations == 0:
            recommendations.append(
                Recommendation(
                    action="checkpoint_quiz",
                    topic_slug=estimate.topic_slug,
                    topic_name=estimate.topic_name,
                    reason=f"No questions answered on {estimate.topic_name} yet.",
                    priority=15,
                )
            )
        elif label == "unknown":
            recommendations.append(
                Recommendation(
                    action="additional_practice",
                    topic_slug=estimate.topic_slug,
                    topic_name=estimate.topic_name,
                    reason=(
                        f"Only {estimate.observations} answer"
                        f"{'s' if estimate.observations != 1 else ''} so far — not enough to "
                        "judge this topic."
                    ),
                    priority=18,
                )
            )
        elif label == "developing":
            recommendations.append(
                Recommendation(
                    action="practise_harder_questions",
                    topic_slug=estimate.topic_slug,
                    topic_name=estimate.topic_name,
                    reason=f"{estimate.topic_name} is close; harder questions will confirm it.",
                    priority=12,
                )
            )
        else:
            recommendations.append(
                Recommendation(
                    action="advance",
                    topic_slug=estimate.topic_slug,
                    topic_name=estimate.topic_name,
                    reason=f"{estimate.topic_name} looks solid.",
                    priority=5,
                )
            )

    recommendations.sort(key=lambda r: r.priority, reverse=True)
    return recommendations


# --- Spaced repetition ---------------------------------------------------
#: Ratings follow the SM-2 convention: 0 again, 1 hard, 2 good, 3 easy.
def schedule_review(
    *, ease: float, interval_days: int, repetitions: int, rating: int
) -> tuple[float, int, int, datetime]:
    """Return the next ``(ease, interval_days, repetitions, due_at)``.

    A modified SM-2: a lapse resets the interval but only partly reduces ease,
    so one bad day does not destroy a card's schedule.
    """
    rating = max(0, min(3, rating))

    if rating == 0:
        new_ease = max(1.3, ease - 0.20)
        return new_ease, 0, 0, datetime.now(UTC) + timedelta(minutes=10)

    adjustment = {1: -0.15, 2: 0.0, 3: 0.15}[rating]
    new_ease = max(1.3, min(3.0, ease + adjustment))
    new_repetitions = repetitions + 1

    if new_repetitions == 1:
        new_interval = 1
    elif new_repetitions == 2:
        new_interval = 3 if rating >= 2 else 2
    else:
        new_interval = max(1, round(max(interval_days, 1) * new_ease))
    if rating == 1:
        new_interval = max(1, round(new_interval * 0.6))

    new_interval = min(new_interval, 365)
    return new_ease, new_interval, new_repetitions, datetime.now(UTC) + timedelta(days=new_interval)
