"""The assignment planner's grounding rule.

The planner's value is the line it draws between "your slides show this" and
"this goes beyond your slides". Everything here pins where that line falls,
because a plan that calls an unsupported step grounded is worse than no plan.

The model is replaced with scripted output; the retriever with a fixed pool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.assignments import planner
from app.assignments.planner import (
    _extract_requirements_heuristically,
    _parse_json_object,
    build_plan,
)
from app.config import Settings
from app.models.base import GenerationRequest, GenerationResult, ProviderInfo
from app.rag.retriever import RetrievalOutcome, RetrievedChunk


# --- doubles ----------------------------------------------------------------
def chunk(chunk_id: str, text: str, page: int = 1, title: str = "Lecture 3") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        text=text,
        page_number=page,
        document_id="doc-lecture",
        document_title=title,
        section="",
        topic_slug="",
        element_type="paragraph",
        bbox=None,
        score=0.5,
        retrievers=["dense"],
        parent_text=text,
    )


class FixedRetriever:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks
        self.calls: list[dict] = []

    def retrieve(self, query, *, user_id, course_ids=None, document_ids=None, top_k=None):
        self.calls.append({"query": query, "document_ids": document_ids})
        return RetrievalOutcome(
            chunks=list(self.chunks),
            query=query,
            query_class="general",
            sufficient=True,
            reason="",
            reformulated_query="",
        )


@dataclass
class ScriptedModel:
    """Answers the requirements call, then the plan call, with canned JSON."""

    requirements: dict
    plan: dict
    generative: bool = True
    requests: list[GenerationRequest] = field(default_factory=list)

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            key="scripted",
            label="Scripted",
            sends_data_off_device=False,
            available=True,
            generative=self.generative,
            model="scripted-1",
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        payload = self.requirements if len(self.requests) == 1 else self.plan
        return GenerationResult(
            text=json.dumps(payload),
            provider="scripted",
            model="scripted-1",
            generative=True,
            input_tokens=10,
            output_tokens=20,
        )


class Extractive:
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            key="extractive",
            label="Quoting",
            sends_data_off_device=False,
            available=True,
            generative=False,
        )

    def generate(self, request):  # pragma: no cover - never called for a non-generative provider
        raise AssertionError("the planner must not call generate() on a non-generative provider")


POOL = [
    chunk(
        "c1",
        "The Fourier transform decomposes a signal into frequency components. In MATLAB use fft.",
    ),
    chunk(
        "c2",
        "A low-pass Butterworth filter removes high frequency noise; "
        "design it with butter and apply with filtfilt.",
        page=7,
    ),
    chunk(
        "c3",
        "The report must present the method, results with labelled plots, and a discussion.",
        page=12,
    ),
]

BRIEF = (
    "Coursework 2: Signal Processing\n"
    "1. Record a 5 second audio clip and load it in MATLAB.\n"
    "2. Compute and plot its frequency spectrum.\n"
    "3. Remove high-frequency noise with a suitable filter.\n"
    "4. Write a 1500 word report with method, results and discussion.\n"
)

REQS = {
    "summary": "Record audio, analyse its spectrum, filter it, and write it up.",
    "requirements": [
        {
            "id": "r1",
            "text": "Load a recorded clip in MATLAB",
            "kind": "tool",
            "quote": "load it in MATLAB",
        },
        {
            "id": "r2",
            "text": "Compute and plot the frequency spectrum",
            "kind": "deliverable",
            "quote": "plot its frequency spectrum",
        },
        {
            "id": "r3",
            "text": "Remove high-frequency noise with a filter",
            "kind": "deliverable",
            "quote": "Remove high-frequency noise",
        },
        {
            "id": "r4",
            "text": "1500 word report with method, results, discussion",
            "kind": "deliverable",
            "quote": "1500 word report",
        },
    ],
}


def settings() -> Settings:
    return Settings(data_dir="/tmp/studypilot-planner-tests")


# --- grounding ---------------------------------------------------------------
def test_a_step_that_cites_covering_passages_is_grounded():
    model = ScriptedModel(
        REQS,
        {
            "steps": [
                {
                    "title": "Compute the frequency spectrum",
                    "instruction": (
                        "Use the Fourier transform to find the frequency components of the "
                        "signal, with MATLAB's fft [1]."
                    ),
                    "why": "r2",
                    "requirement_ids": ["r2"],
                    "beyond_slides": False,
                    "beyond_reason": "",
                },
            ]
        },
    )
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

    step = draft.steps[0]
    assert step.grounding == "slides"
    assert step.approval == "not_required"
    assert step.disclosure == ""
    assert [c.marker for c in step.citations] == [1]
    assert step.citations[0].page_number == 1
    assert draft.status == "ready"
    assert draft.generative is True


def test_a_step_the_model_flags_as_beyond_is_never_grounded_even_with_citations():
    """The model's own flag can only hold a step back, never promote it."""
    model = ScriptedModel(
        REQS,
        {
            "steps": [
                {
                    "title": "Compute the frequency spectrum",
                    "instruction": (
                        "Use the Fourier transform to find the frequency components with fft [1]."
                    ),
                    "why": "",
                    "requirement_ids": ["r2"],
                    "beyond_slides": True,
                    "beyond_reason": "Windowing is not covered",
                },
            ]
        },
    )
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

    step = draft.steps[0]
    assert step.grounding == "beyond"
    assert step.approval == "pending"
    assert step.model_flagged_beyond is True
    assert "Windowing is not covered" in step.disclosure
    assert "not in your lecture slides" in step.disclosure or "do not cover" in step.disclosure
    assert draft.status == "needs_approval"


def test_a_step_with_no_citation_is_beyond_with_a_disclosure_naming_what_is_missing():
    model = ScriptedModel(
        REQS,
        {
            "steps": [
                {
                    "title": "Apply a wavelet denoising stage",
                    "instruction": (
                        "Decompose the signal with a discrete wavelet transform and "
                        "threshold the detail coefficients."
                    ),
                    "why": "",
                    "requirement_ids": ["r3"],
                    "beyond_slides": False,
                    "beyond_reason": "",
                },
            ]
        },
    )
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

    step = draft.steps[0]
    assert step.grounding == "beyond"
    assert step.citations == []
    assert "wavelet" in step.disclosure
    assert "not in your lecture slides" in step.disclosure


def test_a_step_whose_citations_do_not_cover_its_terms_is_beyond():
    """Citing a real passage about something else does not make a step grounded."""
    model = ScriptedModel(
        REQS,
        {
            "steps": [
                {
                    "title": "Apply a wavelet denoising stage",
                    "instruction": (
                        "Decompose the signal with a discrete wavelet transform and "
                        "threshold the detail coefficients [3]."
                    ),
                    "why": "",
                    "requirement_ids": ["r3"],
                    "beyond_slides": False,
                    "beyond_reason": "",
                },
            ]
        },
    )
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

    step = draft.steps[0]
    assert step.grounding == "beyond"
    assert step.coverage < 0.34
    assert step.citations[0].marker == 3  # kept, so the student can see what was cited
    assert "cites your slides, but they do not cover" in step.disclosure


def test_a_fabricated_citation_marker_is_stripped():
    model = ScriptedModel(
        REQS,
        {
            "steps": [
                {
                    "title": "Compute the frequency spectrum",
                    "instruction": "Use the Fourier transform with fft [1] as shown in [9].",
                    "why": "",
                    "requirement_ids": ["r2"],
                    "beyond_slides": False,
                    "beyond_reason": "",
                },
            ]
        },
    )
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

    step = draft.steps[0]
    assert "[9]" not in step.instruction
    assert "[1]" in step.instruction
    assert [c.marker for c in step.citations] == [1]


# --- scope ------------------------------------------------------------------
def test_retrieval_is_scoped_to_lecture_material_only():
    retriever = FixedRetriever(POOL)
    model = ScriptedModel(
        REQS, {"steps": [{"title": "t", "instruction": "i [1]", "requirement_ids": []}]}
    )
    build_plan(
        settings=settings(),
        llm=model,
        retriever=retriever,
        user_id="u",
        course_id="c",
        brief_title="CW2",
        brief_text=BRIEF,
        lecture_document_ids=["doc-lecture", "doc-textbook"],
    )

    assert retriever.calls, "one retrieval per requirement"
    assert all(call["document_ids"] == ["doc-lecture", "doc-textbook"] for call in retriever.calls)


def test_without_lecture_material_every_step_is_beyond_and_the_plan_says_why():
    model = ScriptedModel(
        REQS,
        {
            "steps": [
                {
                    "title": "Compute the spectrum",
                    "instruction": "Use fft.",
                    "requirement_ids": ["r2"],
                },
            ]
        },
    )
    draft = build_plan(
        settings=settings(),
        llm=model,
        retriever=FixedRetriever([]),
        user_id="u",
        course_id="c",
        brief_title="CW2",
        brief_text=BRIEF,
        lecture_document_ids=[],
    )

    assert draft.steps[0].grounding == "beyond"
    assert any("no lecture material" in w for w in draft.warnings)


# --- without a model --------------------------------------------------------
def test_without_a_generative_model_the_plan_is_honest_about_what_it_is():
    draft = build_plan(
        settings=settings(),
        llm=Extractive(),
        retriever=FixedRetriever(POOL),
        user_id="u",
        course_id="c",
        brief_title="CW2",
        brief_text=BRIEF,
        lecture_document_ids=["doc-lecture"],
    )

    assert draft.generative is False
    assert draft.provider == "extractive"
    assert any("No language model was used" in w for w in draft.warnings)
    # The brief's numbered lines became requirements, and matching ones cite passages.
    assert len(draft.requirements) >= 3
    titles = " ".join(s.title.lower() for s in draft.steps)
    assert "spectrum" in titles and "report" in titles
    grounded = [s for s in draft.steps if s.grounding == "slides"]
    assert grounded, "a requirement with matching slide text should be grounded"


def test_heuristic_requirements_read_numbered_lines():
    _summary, reqs = _extract_requirements_heuristically(BRIEF)
    assert [r.id for r in reqs] == ["r1", "r2", "r3", "r4"]
    assert reqs[0].kind == "tool"  # mentions MATLAB
    assert "spectrum" in reqs[1].text


def test_an_empty_brief_fails_cleanly():
    draft = build_plan(
        settings=settings(),
        llm=Extractive(),
        retriever=FixedRetriever(POOL),
        user_id="u",
        course_id="c",
        brief_title="CW2",
        brief_text="   ",
        lecture_document_ids=["doc-lecture"],
    )
    assert draft.status == "failed"
    assert draft.steps == []


# --- model output handling ------------------------------------------------
def test_json_is_read_through_code_fences_and_preamble():
    text = (
        'Here you go:\n```json\n{"steps": [{"title": "a", "instruction": "b"}]}\n```\n'
        "Hope that helps."
    )
    assert _parse_json_object(text)["steps"][0]["title"] == "a"


def test_non_json_output_falls_back_rather_than_failing():
    class Rambler(ScriptedModel):
        def generate(self, request):
            self.requests.append(request)
            return GenerationResult(
                text="I would rather chat.", provider="s", model="m", generative=True
            )

    draft = build_plan(
        settings=settings(),
        llm=Rambler(REQS, {}),
        retriever=FixedRetriever(POOL),
        user_id="u",
        course_id="c",
        brief_title="CW2",
        brief_text=BRIEF,
        lecture_document_ids=["doc-lecture"],
    )
    assert draft.generative is False
    assert any("could not be read" in w for w in draft.warnings)
    assert draft.steps, "the heuristic plan still stands in"


def test_the_disclosure_is_written_by_code_not_the_model():
    """Same inputs, same wording — it cannot be softened by a prompt."""
    a = planner._disclosure_for(
        title="x", missing_terms=["wavelet"], model_reason="", had_citations=False
    )
    b = planner._disclosure_for(
        title="x", missing_terms=["wavelet"], model_reason="", had_citations=False
    )
    assert a == b
    assert a.startswith(
        "If you follow this step you will be using wavelet, which is not in your lecture slides"
    )
    assert a.endswith("before approving it.")
