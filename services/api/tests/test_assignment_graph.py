"""The planner's state machine: its one corrective pass, and its memory.

The node implementations are tested in test_assignment_planner.py. Here the
subject is the graph's own decisions: when it goes round again, that it
goes round at most once, and how the student's earlier decisions are
applied — by code, to matching steps, never invented.
"""

from __future__ import annotations

import json

from app.assignments.graph import _apply_prior_decisions, _similar
from app.assignments.planner import PlanStep, build_plan
from app.models.base import GenerationRequest, GenerationResult, ProviderInfo
from tests.test_assignment_planner import (
    BRIEF,
    POOL,
    REQS,
    FixedRetriever,
    chunk,
    settings,
)


class CountingModel:
    """Scripted requirements, then a scripted plan for each draft call."""

    def __init__(self, plans: list[dict]) -> None:
        self.plans = plans
        self.calls = 0

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            key="scripted",
            label="Scripted",
            sends_data_off_device=False,
            available=True,
            generative=True,
            model="scripted-1",
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls += 1
        payload = REQS if self.calls == 1 else self.plans[min(self.calls - 2, len(self.plans) - 1)]
        return GenerationResult(
            text=json.dumps(payload), provider="scripted", model="scripted-1", generative=True
        )


class GrowingRetriever(FixedRetriever):
    """Returns extra, better-matching passages on the corrective queries."""

    def __init__(self, pool, extra):
        super().__init__(pool)
        self.extra = extra

    def retrieve(self, query, *, user_id, course_ids=None, document_ids=None, top_k=None):
        outcome = super().retrieve(
            query, user_id=user_id, course_ids=course_ids, document_ids=document_ids, top_k=top_k
        )
        if any(word in query.lower() for word in ("wavelet", "denoising")):
            outcome.chunks = list(outcome.chunks) + list(self.extra)
        return outcome


def weak_plan():
    # Two steps cite real passages that do not cover their wording.
    return {
        "steps": [
            {
                "title": "Apply wavelet denoising",
                "instruction": "Use a discrete wavelet transform to denoise the clip [1].",
                "requirement_ids": ["r3"],
                "beyond_slides": False,
            },
            {
                "title": "Threshold the wavelet detail coefficients",
                "instruction": "Soft-threshold the detail coefficients before reconstruction [2].",
                "requirement_ids": ["r3"],
                "beyond_slides": False,
            },
            {
                "title": "Filter the noise",
                "instruction": "Design a Butterworth low-pass filter with butter and filtfilt [2].",
                "requirement_ids": ["r3"],
                "beyond_slides": False,
            },
        ]
    }


def test_two_coverage_only_beyond_steps_trigger_exactly_one_corrective_pass():
    wavelet_chunk = chunk(
        "c9",
        "Wavelet denoising: take the discrete wavelet transform, soft-threshold the detail "
        "coefficients, and reconstruct the signal.",
        page=11,
    )
    # After the second retrieval the model cites the new passage.
    second = weak_plan()
    second["steps"][0]["instruction"] = "Use a discrete wavelet transform to denoise the clip [4]."
    second["steps"][1]["instruction"] = (
        "Soft-threshold the detail coefficients before reconstruction [4]."
    )
    model = CountingModel([weak_plan(), second])
    retriever = GrowingRetriever(POOL, [wavelet_chunk])

    draft = build_plan(
        settings=settings(),
        llm=model,
        retriever=retriever,
        user_id="u",
        course_id="c",
        brief_title="CW2",
        brief_text=BRIEF,
        lecture_document_ids=["doc-lecture"],
    )

    # requirements + draft + one corrective draft = three model calls, no more.
    assert model.calls == 3
    assert any("second retrieval pass" in w for w in draft.warnings)
    assert draft.steps[0].grounding == "slides"
    assert draft.steps[0].citations[0].page_number == 11
    assert draft.steps[2].grounding == "slides"


def test_the_corrective_pass_never_runs_twice():
    """Steps still uncovered after the second pass are beyond, and that is final."""
    model = CountingModel([weak_plan(), weak_plan()])
    draft = build_plan(
        settings=settings(),
        llm=model,
        retriever=FixedRetriever(POOL),
        user_id="u",
        course_id="c",
        brief_title="CW2",
        brief_text=BRIEF,
        lecture_document_ids=["doc-lecture"],
    )
    assert model.calls == 3
    assert draft.steps[0].grounding == "beyond"
    assert draft.status == "needs_approval"


def test_one_weak_step_is_not_worth_a_second_model_call():
    plan = weak_plan()
    plan["steps"] = plan["steps"][1:]  # one coverage-only beyond step, one grounded
    model = CountingModel([plan])
    build_plan(
        settings=settings(),
        llm=model,
        retriever=FixedRetriever(POOL),
        user_id="u",
        course_id="c",
        brief_title="CW2",
        brief_text=BRIEF,
        lecture_document_ids=["doc-lecture"],
    )
    assert model.calls == 2


# --- memory -----------------------------------------------------------------
def test_a_declined_step_stays_declined_on_the_next_draft():
    model = CountingModel([weak_plan()])
    draft = build_plan(
        settings=settings(),
        llm=model,
        retriever=FixedRetriever(POOL),
        user_id="u",
        course_id="c",
        brief_title="CW2",
        brief_text=BRIEF,
        lecture_document_ids=["doc-lecture"],
        prior_decisions=[
            {"title": "Apply wavelet denoising", "approval": "declined", "missing_terms": []},
            {"title": "Threshold wavelet detail coefficients", "approval": "approved"},
        ],
    )
    assert draft.steps[0].approval == "declined"
    assert "declined this step on an earlier draft" in draft.steps[0].disclosure
    # Near-identical title (one word dropped): still recognised.
    assert draft.steps[1].approval == "approved"
    assert any("kept the decision" in w for w in draft.warnings)


def test_prior_decisions_reach_the_prompt():
    captured: list[str] = []

    class Capturing(CountingModel):
        def generate(self, request):
            captured.append(request.user)
            return super().generate(request)

    build_plan(
        settings=settings(),
        llm=Capturing([weak_plan()]),
        retriever=FixedRetriever(POOL),
        user_id="u",
        course_id="c",
        brief_title="CW2",
        brief_text=BRIEF,
        lecture_document_ids=["doc-lecture"],
        prior_decisions=[{"title": "Use a neural vocoder", "approval": "declined"}],
        accepted_gaps=["batch", "normalisation"],
    )
    plan_prompt = captured[1]
    assert "DECLINED" in plan_prompt and "Use a neural vocoder" in plan_prompt
    assert "already accepted that the slides do not cover: batch, normalisation" in plan_prompt


def test_memory_never_touches_grounded_steps_or_invents_decisions():
    grounded = PlanStep(
        index=1, title="Use fft", instruction="x", grounding="slides", approval="not_required"
    )
    pending = PlanStep(
        index=2, title="Use wavelets", instruction="x", grounding="beyond", approval="pending"
    )
    carried = _apply_prior_decisions(
        [grounded, pending],
        [
            {"title": "Use fft", "approval": "declined"},
            {"title": "Something else", "approval": "approved"},
        ],
    )
    assert carried == 0
    assert grounded.approval == "not_required"
    assert pending.approval == "pending"


def test_title_similarity_is_forgiving_but_not_loose():
    assert _similar("Apply wavelet denoising", "apply wavelet denoising.")
    assert _similar(
        "Train the baseline network and log accuracy",
        "Train the baseline network, logging accuracy",
    )
    assert not _similar("Write the report", "Train the network")
