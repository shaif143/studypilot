"""Answer evaluation.

Marking is deterministic wherever the question shape allows it, because a
student's progress record is high-stakes and must be reproducible:

* ``mcq``          exact option comparison
* ``numeric``      absolute/relative tolerance comparison
* ``short_answer`` keyword and synonym matching against the rubric
* ``explanation``  rubric-point coverage; optionally refined by a model, but the
                   deterministic score is always computed first and is what is
                   used if the model's output does not validate.

Partial credit is produced for written answers so that a half-correct
explanation feeds the knowledge model as a half-observation rather than a
failure.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field

from app.models.base import GenerationRequest, LLMProvider, ProviderUnavailableError
from app.models.llm import extract_json_object
from app.storage.models import Question

log = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[A-Za-z0-9']+")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


@dataclass
class GradedResponse:
    awarded_marks: float
    max_marks: float
    correct: bool | None
    marking_method: str  # deterministic | rubric | llm_rubric | unmarked
    feedback: str
    #: 0..1 fraction of the available marks, fed to the knowledge model.
    credit: float = 0.0
    matched_points: list[str] = field(default_factory=list)
    missed_points: list[str] = field(default_factory=list)


def _normalise(text: str) -> list[str]:
    return [token.lower() for token in _WORD_RE.findall(text or "")]


def _contains_phrase(answer_tokens: list[str], phrase: str) -> bool:
    phrase_tokens = _normalise(phrase)
    if not phrase_tokens:
        return False
    if len(phrase_tokens) == 1:
        token = phrase_tokens[0]
        # Allow a short suffix difference so "convolution" matches "convolutions".
        return any(
            candidate == token
            or (candidate.startswith(token) and len(candidate) - len(token) <= 3)
            or (token.startswith(candidate) and len(token) - len(candidate) <= 3)
            for candidate in answer_tokens
        )
    window = len(phrase_tokens)
    for start in range(len(answer_tokens) - window + 1):
        if answer_tokens[start : start + window] == phrase_tokens:
            return True
    # Fall back to requiring most of the phrase's words to be present.
    present = sum(1 for token in phrase_tokens if token in answer_tokens)
    return present >= max(1, math.ceil(window * 0.75))


def grade_response(
    question: Question,
    *,
    selected_option: int | None = None,
    answer_text: str = "",
    numeric_value: float | None = None,
    llm: LLMProvider | None = None,
) -> GradedResponse:
    """Mark one response. ``llm`` is optional and only refines written answers."""
    max_marks = float(question.marks or 1)

    if question.kind == "mcq":
        return _grade_mcq(question, selected_option, max_marks)
    if question.kind == "numeric":
        return _grade_numeric(question, numeric_value, answer_text, max_marks)
    return _grade_written(question, answer_text, max_marks, llm)


def _grade_mcq(question: Question, selected: int | None, max_marks: float) -> GradedResponse:
    if selected is None:
        return GradedResponse(
            awarded_marks=0.0,
            max_marks=max_marks,
            correct=False,
            marking_method="deterministic",
            feedback="No option selected.",
            credit=0.0,
        )
    correct = question.correct_option is not None and selected == question.correct_option
    if correct:
        feedback = "Correct."
    else:
        expected = ""
        if question.correct_option is not None and question.options:
            try:
                expected = str(question.options[question.correct_option])
            except (IndexError, TypeError):
                expected = question.expected_answer
        feedback = f"Not correct. The answer is: {expected}" if expected else "Not correct."
        if question.explanation:
            feedback = f"{feedback}\n\n{question.explanation}"
    return GradedResponse(
        awarded_marks=max_marks if correct else 0.0,
        max_marks=max_marks,
        correct=correct,
        marking_method="deterministic",
        feedback=feedback,
        credit=1.0 if correct else 0.0,
    )


def _grade_numeric(
    question: Question, value: float | None, answer_text: str, max_marks: float
) -> GradedResponse:
    if value is None and answer_text:
        match = _NUMBER_RE.search(answer_text.replace(",", ""))
        if match:
            try:
                value = float(match.group(0))
            except ValueError:
                value = None
    if value is None or question.numeric_answer is None:
        return GradedResponse(
            awarded_marks=0.0,
            max_marks=max_marks,
            correct=None,
            marking_method="unmarked",
            feedback="No number could be read from the answer, so it was not marked.",
            credit=0.0,
        )
    expected = float(question.numeric_answer)
    tolerance = abs(float(question.numeric_tolerance or 0.01))
    # Relative tolerance for large magnitudes, absolute for values near zero.
    allowed = max(tolerance, abs(expected) * tolerance)
    correct = abs(value - expected) <= allowed
    feedback = (
        "Correct."
        if correct
        else f"Not correct. Expected {expected:g} (± {allowed:g}); you answered {value:g}."
    )
    if not correct and question.explanation:
        feedback = f"{feedback}\n\n{question.explanation}"
    return GradedResponse(
        awarded_marks=max_marks if correct else 0.0,
        max_marks=max_marks,
        correct=correct,
        marking_method="deterministic",
        feedback=feedback,
        credit=1.0 if correct else 0.0,
    )


def _rubric_points(question: Question) -> list[str]:
    rubric = question.rubric or {}
    points = rubric.get("points") or rubric.get("required") or rubric.get("accept_any") or []
    if isinstance(points, list) and points:
        return [str(point) for point in points if str(point).strip()]
    if question.expected_answer:
        return [question.expected_answer]
    return []


def _grade_written(
    question: Question, answer_text: str, max_marks: float, llm: LLMProvider | None
) -> GradedResponse:
    answer = (answer_text or "").strip()
    points = _rubric_points(question)

    if not answer:
        return GradedResponse(
            awarded_marks=0.0,
            max_marks=max_marks,
            correct=False,
            marking_method="rubric",
            feedback="No answer given.",
            credit=0.0,
            missed_points=points,
        )
    if not points:
        return GradedResponse(
            awarded_marks=0.0,
            max_marks=max_marks,
            correct=None,
            marking_method="unmarked",
            feedback=("This question has no marking rubric, so it was not marked automatically."),
            credit=0.0,
        )

    tokens = _normalise(answer)
    matched = [point for point in points if _contains_phrase(tokens, point)]
    missed = [point for point in points if point not in matched]
    credit = len(matched) / len(points)

    # The deterministic score stands unless a model produces a *valid* refinement.
    method = "rubric"
    feedback_parts: list[str] = []
    if matched:
        feedback_parts.append(f"Covered: {', '.join(matched)}.")
    if missed:
        feedback_parts.append(f"Not covered: {', '.join(missed)}.")

    if llm is not None and llm.info.generative and len(answer) >= 40:
        refined = _llm_refine(question, answer, points, llm)
        if refined is not None:
            credit, model_feedback = refined
            method = "llm_rubric"
            feedback_parts = [model_feedback]

    awarded = round(max_marks * credit, 2)
    correct: bool | None = True if credit >= 0.999 else (False if credit <= 0.001 else None)
    if question.explanation and credit < 0.999:
        feedback_parts.append(question.explanation)

    return GradedResponse(
        awarded_marks=awarded,
        max_marks=max_marks,
        correct=correct,
        marking_method=method,
        feedback=" ".join(feedback_parts).strip(),
        credit=credit,
        matched_points=matched,
        missed_points=missed,
    )


def _llm_refine(
    question: Question, answer: str, points: list[str], llm: LLMProvider
) -> tuple[float, str] | None:
    """Ask a model to apply the rubric. Returns ``None`` if it cannot be trusted."""
    rubric_text = "\n".join(f"- {point}" for point in points)
    system = (
        "You mark a student's written answer strictly against a rubric. "
        "Award a point only if the answer genuinely contains that idea. "
        "Be fair about wording differences. Reply with JSON only."
    )
    user = (
        f"Question:\n{question.prompt}\n\n"
        f"Rubric points ({len(points)} total):\n{rubric_text}\n\n"
        f"Student answer:\n{answer}\n\n"
        'Reply as {"points_awarded": <integer>, "feedback": "<two sentences>"}'
    )
    try:
        result = llm.generate(
            GenerationRequest(system=system, user=user, max_output_tokens=400, temperature=0.0)
        )
    except ProviderUnavailableError as exc:
        log.warning("rubric_marking_failed", extra={"reason": str(exc)})
        return None

    payload = extract_json_object(result.text)
    if not isinstance(payload, dict):
        return None
    try:
        awarded = int(payload["points_awarded"])
    except (KeyError, TypeError, ValueError):
        return None
    if not 0 <= awarded <= len(points):
        return None
    feedback = str(payload.get("feedback", "")).strip()
    if not feedback:
        return None
    return awarded / len(points), feedback
