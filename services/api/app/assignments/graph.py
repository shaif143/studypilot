"""The assignment planner as a LangGraph state machine.

    prepare ─▶ requirements ─▶ retrieve ─▶ draft ─▶ ground ─┬─▶ finish ─▶ END
                                   ▲                        │
                                   └──── one corrective pass ┘

Same discipline as the tutor's graph (`app.agents.graph`): small, bounded,
no tool calling, and deterministic code owns every decision that matters.
The model writes the requirement list and the step text; code decides the
scope of retrieval, whether a step is grounded, whether a second pass is
worth it, and what the student's earlier decisions mean for this draft.

The corrective pass exists for one failure the first real runs showed: a
step the lecture material does cover, but whose wording did not match the
passages retrieved for the *requirements*, so the coverage check held it
back. When two or more steps look like that, the graph retrieves once more
using those steps' own words, re-drafts against the larger pool, and
re-grounds. Once — a step that is still not covered after that is beyond
the slides and is said to be.

Memory is in the state, not in the model: the student's decisions on the
previous draft of this brief, and the gaps they have already accepted in
this course, are passed in by the route and applied in `ground` (a step with
the same title as one they declined stays declined; one they approved stays
approved) and shown to the model in `draft` so it does not re-propose what
was declined.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, NotRequired, TypedDict

from langgraph.graph import END, StateGraph

from app.assignments import planner as p
from app.config import Settings
from app.models.base import ConsentRequiredError, LLMProvider, ProviderUnavailableError
from app.rag.retriever import HybridRetriever, RetrievedChunk
from app.rag.sparse import content_terms

log = logging.getLogger(__name__)

#: Coverage-only "beyond" steps needed before a second pass is worth a model
#: call. One is noise; two or more suggests the requirement-driven retrieval
#: missed material the steps rely on.
CORRECTIVE_THRESHOLD = 2


def _extend(current: list[Any] | None, incoming: list[Any] | None) -> list[Any]:
    return list(current or []) + list(incoming or [])


class PlanState(TypedDict):
    # --- inputs -----------------------------------------------------------
    user_id: str
    course_id: str
    brief_title: str
    brief_text: str
    lecture_document_ids: list[str]
    integrity_policy: str
    #: Decisions the student made on the previous draft of this brief:
    #: {"title", "approval", "missing_terms"}.
    prior_decisions: list[dict[str, Any]]
    #: Terms the student has already accepted as beyond the slides, anywhere
    #: in this course. Shown to the model; never used to auto-approve.
    accepted_gaps: list[str]

    # --- working ----------------------------------------------------------
    generative: NotRequired[bool]
    summary: NotRequired[str]
    requirements: NotRequired[list[p.Requirement]]
    pool: NotRequired[list[RetrievedChunk]]
    raw_steps: NotRequired[list[dict[str, Any]]]
    steps: NotRequired[list[p.PlanStep]]
    #: Queries for the corrective retrieval, set by `ground`.
    extra_queries: NotRequired[list[str]]
    corrective_done: NotRequired[bool]

    # --- accounting -------------------------------------------------------
    warnings: Annotated[list[str], _extend]
    input_tokens: NotRequired[int]
    output_tokens: NotRequired[int]
    failed: NotRequired[bool]


@dataclass
class PlannerDependencies:
    settings: Settings
    llm: LLMProvider
    retriever: HybridRetriever
    release_db: Callable[[], None] | None = None


def build_planner_graph(deps: PlannerDependencies):
    graph: StateGraph = StateGraph(PlanState)
    graph.add_node("prepare", _make_prepare(deps))
    graph.add_node("requirements", _make_requirements(deps))
    graph.add_node("retrieve", _make_retrieve(deps))
    graph.add_node("draft", _make_draft(deps))
    graph.add_node("ground", _make_ground(deps))
    graph.add_node("finish", _finish)

    graph.set_entry_point("prepare")
    graph.add_conditional_edges(
        "prepare", lambda s: "finish" if s.get("failed") else "requirements"
    )
    graph.add_edge("requirements", "retrieve")
    graph.add_edge("retrieve", "draft")
    graph.add_edge("draft", "ground")
    graph.add_conditional_edges(
        "ground", lambda s: "retrieve" if s.get("extra_queries") else "finish"
    )
    graph.add_edge("finish", END)
    return graph.compile()


# --- nodes ----------------------------------------------------------------
def _make_prepare(deps: PlannerDependencies):
    def prepare(state: PlanState) -> dict[str, Any]:
        text = state["brief_text"].strip()
        warnings: list[str] = []
        if not text:
            return {
                "failed": True,
                "warnings": [
                    "No text could be read from this brief, so there is nothing to plan from."
                ],
            }
        if len(text) > p.MAX_BRIEF_CHARS:
            warnings.append(
                f"The brief is long; only its first {p.MAX_BRIEF_CHARS:,} characters were used."
            )
            text = text[: p.MAX_BRIEF_CHARS]
        if not state["lecture_document_ids"]:
            warnings.append(
                "This course has no lecture material yet, so no step can be grounded. "
                "Upload the lectures first for a guide that points to your slides."
            )
        return {
            "brief_text": text,
            "generative": bool(getattr(deps.llm.info, "generative", False)),
            "warnings": warnings,
            "input_tokens": 0,
            "output_tokens": 0,
            "corrective_done": False,
        }

    return prepare


def _make_requirements(deps: PlannerDependencies):
    def requirements(state: PlanState) -> dict[str, Any]:
        out: dict[str, Any] = {"warnings": []}
        generative = bool(state.get("generative"))
        summary, reqs = "", []
        if generative:
            try:
                if deps.release_db:
                    deps.release_db()
                summary, reqs, used = p._extract_requirements_with_model(
                    deps.llm, state["brief_title"], state["brief_text"]
                )
                out["input_tokens"] = state.get("input_tokens", 0) + used[0]
                out["output_tokens"] = state.get("output_tokens", 0) + used[1]
            except (ProviderUnavailableError, ConsentRequiredError) as exc:
                out["warnings"].append(f"The model could not be used: {exc}")
                out["generative"] = False
            except p._BadModelOutput as exc:
                out["warnings"].append(f"The model's requirement list could not be read ({exc}).")
                out["generative"] = False
        if not reqs:
            summary, reqs = p._extract_requirements_heuristically(state["brief_text"])
            if not reqs:
                reqs = [
                    p.Requirement(
                        id="r1", text=summary[:300] or "Complete the assignment as briefed."
                    )
                ]
        out["summary"] = summary
        out["requirements"] = reqs
        return out

    return requirements


def _make_retrieve(deps: PlannerDependencies):
    def retrieve(state: PlanState) -> dict[str, Any]:
        pool = p._gather_evidence(
            deps.retriever,
            state["requirements"],
            user_id=state["user_id"],
            course_id=state["course_id"],
            lecture_document_ids=state["lecture_document_ids"],
        )
        extra = state.get("extra_queries") or []
        if extra:
            # The corrective pass: the steps' own words, merged into the pool.
            by_id = {c.chunk_id: c for c in pool}
            for query in extra:
                outcome = deps.retriever.retrieve(
                    query,
                    user_id=state["user_id"],
                    course_ids=[state["course_id"]],
                    document_ids=state["lecture_document_ids"],
                    top_k=p.PER_REQUIREMENT_TOP_K,
                )
                for chunk in outcome.chunks:
                    if chunk.chunk_id not in by_id or chunk.score > by_id[chunk.chunk_id].score:
                        by_id[chunk.chunk_id] = chunk
            pool = sorted(by_id.values(), key=lambda c: c.score, reverse=True)
        return {"pool": pool, "extra_queries": []}

    return retrieve


def _make_draft(deps: PlannerDependencies):
    def draft(state: PlanState) -> dict[str, Any]:
        out: dict[str, Any] = {"warnings": []}
        generative = bool(state.get("generative"))
        pool = state.get("pool") or []
        raw: list[dict[str, Any]] = []
        if generative:
            try:
                if deps.release_db:
                    deps.release_db()
                raw, used = p._draft_steps_with_model(
                    deps.llm,
                    brief_title=state["brief_title"],
                    brief_text=state["brief_text"],
                    summary=state.get("summary", ""),
                    requirements=state["requirements"],
                    snippets=p._snippets_for(pool),
                    integrity_policy=state["integrity_policy"],
                    prior_decisions=state.get("prior_decisions") or [],
                    accepted_gaps=state.get("accepted_gaps") or [],
                )
                out["input_tokens"] = state.get("input_tokens", 0) + used[0]
                out["output_tokens"] = state.get("output_tokens", 0) + used[1]
                note = raw[0].pop("_note", "") if raw else ""
                if note:
                    out["warnings"].append(note)
            except (ProviderUnavailableError, ConsentRequiredError) as exc:
                out["warnings"].append(f"The model could not be used: {exc}")
                out["generative"] = False
                generative = False
            except p._BadModelOutput as exc:
                out["warnings"].append(f"The model's plan could not be read ({exc}).")
                out["generative"] = False
                generative = False
        if not raw:
            raw = p._draft_steps_heuristically(state["requirements"], pool)
            if not generative:
                out["warnings"].append(
                    "No language model was used: these are the brief's requirements with the "
                    "lecture passages that match each one, not a drafted plan. Connect a "
                    "model in Settings for step-by-step guidance."
                )
        out["raw_steps"] = raw
        return out

    return draft


def _make_ground(deps: PlannerDependencies):
    def ground(state: PlanState) -> dict[str, Any]:
        steps = p._ground_steps(state["raw_steps"], state.get("pool") or [], deps.settings)
        warnings: list[str] = []

        # --- memory: the student's decisions on the previous draft -------
        remembered = _apply_prior_decisions(steps, state.get("prior_decisions") or [])
        if remembered:
            warnings.append(
                f"{remembered} step(s) kept the decision you made on the earlier draft."
            )

        # --- one corrective pass, when it can plausibly help ---------------
        weak = [
            s
            for s in steps
            if s.grounding == "beyond"
            and not s.model_flagged_beyond
            and s.citations
            and s.approval == "pending"
        ]
        if (
            state.get("generative")
            and not state.get("corrective_done")
            and len(weak) >= CORRECTIVE_THRESHOLD
        ):
            queries = [f"{s.title} {' '.join(s.missing_terms[:4])}".strip() for s in weak]
            warnings.append(
                f"A second retrieval pass was made for {len(weak)} step(s) whose wording did "
                "not match the passages first retrieved."
            )
            return {
                "steps": steps,
                "extra_queries": queries,
                "corrective_done": True,
                "warnings": warnings,
            }
        return {"steps": steps, "extra_queries": [], "warnings": warnings}

    return ground


def _finish(state: PlanState) -> dict[str, Any]:
    return {}


# --- memory ----------------------------------------------------------------
def _normalise(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", title.lower()).strip()


def _similar(a: str, b: str) -> bool:
    """Same title, or titles sharing most of their content terms."""
    na, nb = _normalise(a), _normalise(b)
    if na == nb:
        return True
    ta, tb = set(content_terms(na)), set(content_terms(nb))
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / len(ta | tb)
    return overlap >= 0.6


def _apply_prior_decisions(steps: list[p.PlanStep], prior: list[dict[str, Any]]) -> int:
    """Carry the student's approve/decline forward onto matching steps.

    Only steps that are `beyond` and still pending take a remembered
    decision; a grounded step needs no approval and is left alone, and a
    decision is never invented. Returns how many were carried.
    """
    carried = 0
    for step in steps:
        if step.grounding != "beyond" or step.approval != "pending":
            continue
        for decision in prior:
            approval = decision.get("approval")
            if approval not in {"approved", "declined"}:
                continue
            if _similar(step.title, str(decision.get("title", ""))):
                step.approval = approval
                step.disclosure += (
                    " You approved this step on an earlier draft of this guide."
                    if approval == "approved"
                    else " You declined this step on an earlier draft, so it stays declined."
                )
                carried += 1
                break
    return carried


# --- entry point -----------------------------------------------------------
def run_planner(
    deps: PlannerDependencies,
    *,
    user_id: str,
    course_id: str,
    brief_title: str,
    brief_text: str,
    lecture_document_ids: list[str],
    integrity_policy: str = "",
    prior_decisions: list[dict[str, Any]] | None = None,
    accepted_gaps: list[str] | None = None,
) -> p.PlanDraft:
    graph = build_planner_graph(deps)
    final: PlanState = graph.invoke(
        PlanState(
            user_id=user_id,
            course_id=course_id,
            brief_title=brief_title,
            brief_text=brief_text,
            lecture_document_ids=list(lecture_document_ids),
            integrity_policy=integrity_policy,
            prior_decisions=list(prior_decisions or []),
            accepted_gaps=list(accepted_gaps or []),
            warnings=[],
        )
    )
    if final.get("failed"):
        return p.PlanDraft(
            summary="",
            requirements=[],
            steps=[],
            provider="none",
            model="",
            generative=False,
            status="failed",
            warnings=list(final.get("warnings") or []),
        )
    generative = bool(final.get("generative"))
    steps = final.get("steps") or []
    return p.PlanDraft(
        summary=final.get("summary", ""),
        requirements=final.get("requirements") or [],
        steps=steps,
        provider=getattr(deps.llm.info, "key", "unknown") if generative else "extractive",
        model=getattr(deps.llm.info, "model", "") if generative else "",
        generative=generative,
        status="needs_approval" if any(s.approval == "pending" for s in steps) else "ready",
        warnings=list(final.get("warnings") or []),
        input_tokens=int(final.get("input_tokens") or 0),
        output_tokens=int(final.get("output_tokens") or 0),
    )
