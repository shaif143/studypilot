"""The Learning Supervisor graph.

A single LangGraph state machine coordinates the retrieval, explanation and
tutoring steps.  It is deliberately small and bounded — there is no autonomous
agent loop, no tool calling, and a fixed maximum of one corrective retrieval
pass — because an unbounded loop over a student's private material buys nothing
and costs tokens, latency and predictability.

    classify ─▶ retrieve ─┬─▶ abstain ──▶ recommend ─▶ END
                          └─▶ generate ─▶ verify ───▶ recommend ─▶ END

Deterministic code owns: scope and permissions, retrieval, sufficiency
judgement, citation verification, and the recommended next action.  The model
owns only the explanation text.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.prompts import build_user_prompt, system_prompt_for
from app.agents.state import TutorState
from app.config import Settings
from app.models.base import (
    ConsentRequiredError,
    EvidenceSnippet,
    GenerationRequest,
    LLMProvider,
    ProviderUnavailableError,
)
from app.rag.retriever import HybridRetriever

log = logging.getLogger(__name__)

_CITATION_RE = re.compile(r"\[(\d{1,2})\]")
#: Evidence is capped so a long slide deck cannot blow the context window.
MAX_EVIDENCE_CHARS = 14_000


@dataclass
class GraphDependencies:
    settings: Settings
    retriever: HybridRetriever
    llm: LLMProvider
    integrity_policy: str = ""
    #: Resolved per request from the user's own settings.
    cloud_consent: bool = False


class LearningGraph:
    """Compiled graph plus the per-request dependencies it closes over."""

    def __init__(self, compiled: Any, dependencies: GraphDependencies) -> None:
        self._compiled = compiled
        self.dependencies = dependencies

    def invoke(self, state: TutorState) -> TutorState:
        return self._compiled.invoke(state)


def build_learning_graph(dependencies: GraphDependencies) -> LearningGraph:
    graph: StateGraph = StateGraph(TutorState)

    graph.add_node("classify", _make_classify(dependencies))
    graph.add_node("retrieve", _make_retrieve(dependencies))
    graph.add_node("generate", _make_generate(dependencies))
    graph.add_node("verify", _make_verify(dependencies))
    graph.add_node("abstain", _make_abstain(dependencies))
    graph.add_node("recommend", _make_recommend(dependencies))

    graph.set_entry_point("classify")
    graph.add_edge("classify", "retrieve")
    graph.add_conditional_edges(
        "retrieve",
        _route_after_retrieve,
        {"generate": "generate", "abstain": "abstain"},
    )
    graph.add_edge("generate", "verify")
    graph.add_edge("verify", "recommend")
    graph.add_edge("abstain", "recommend")
    graph.add_edge("recommend", END)

    return LearningGraph(graph.compile(), dependencies)


# --- nodes ---------------------------------------------------------------
def _make_classify(_deps: GraphDependencies):
    def classify(state: TutorState) -> dict[str, Any]:
        mode = state.get("learning_mode") or "question"
        question = (state.get("student_question") or "").strip()
        # Explain mode with no question is a request to explain the current page.
        if mode == "explain" and not question:
            question = ""
        return {"learning_mode": mode, "student_question": question}

    return classify


def _make_retrieve(deps: GraphDependencies):
    def retrieve(state: TutorState) -> dict[str, Any]:
        question = state.get("student_question") or ""
        mode = state.get("learning_mode")
        document_id = state.get("document_id") or ""

        if mode == "explain":
            # Explaining a page: the page itself is the primary evidence, plus
            # whatever else in the course relates to its heading.
            page_query = state.get("current_topic") or question or "overview"
            outcome = deps.retriever.retrieve(
                page_query,
                user_id=state["user_id"],
                course_ids=[state["course_id"]] if state.get("course_id") else None,
                document_ids=[document_id] if document_id else None,
            )
        else:
            scope_documents = [document_id] if (document_id and mode == "assignment") else None
            outcome = deps.retriever.retrieve(
                question,
                user_id=state["user_id"],
                course_ids=[state["course_id"]] if state.get("course_id") else None,
                document_ids=scope_documents,
            )

        warnings: list[str] = []
        if outcome.reformulated_query and outcome.reformulated_query != outcome.query:
            warnings.append("The question was rephrased to find more of your material.")

        return {
            "retrieved_sources": [chunk.to_dict() for chunk in outcome.chunks],
            "query_class": outcome.query_class,
            "evidence_sufficient": outcome.sufficient,
            "evidence_note": outcome.reason,
            "reformulated_query": outcome.reformulated_query,
            "warnings": warnings,
        }

    return retrieve


def _route_after_retrieve(state: TutorState) -> str:
    return "abstain" if not state.get("retrieved_sources") else "generate"


def _build_snippets(state: TutorState) -> list[EvidenceSnippet]:
    snippets: list[EvidenceSnippet] = []
    budget = MAX_EVIDENCE_CHARS
    for marker, source in enumerate(state.get("retrieved_sources") or [], start=1):
        # Prefer the page-level parent text: a precise child match still arrives
        # with enough surrounding context to be explainable.
        text = source.get("parent_text") or source.get("text", "")
        text = text[: max(500, budget)]
        budget -= len(text)
        snippets.append(
            EvidenceSnippet(
                marker=marker,
                text=text,
                page_number=int(source.get("page_number", 0)),
                document_title=str(source.get("document_title", "")),
                section=str(source.get("section", "")),
            )
        )
        if budget <= 0:
            break
    return snippets


def _make_generate(deps: GraphDependencies):
    def generate(state: TutorState) -> dict[str, Any]:
        snippets = _build_snippets(state)
        mode = state.get("learning_mode") or "question"
        policy = state.get("assignment_policy") or "socratic"

        if mode == "assignment" and policy == "off":
            return {
                "answer": (
                    "Assignment help is switched off for this course. You can turn it "
                    "on in Settings if your course allows it."
                ),
                "grounded": False,
                "generative": False,
                "abstained": True,
                "provider": "policy",
                "model": "",
            }

        system = system_prompt_for(mode, policy)
        user = build_user_prompt(
            question=state.get("student_question") or "",
            snippets=snippets,
            mode=mode,
            explanation_level=state.get("explanation_level") or "intermediate",
            page_number=state.get("current_page"),
            topic=state.get("current_topic") or "",
            hint_level=max(1, int(state.get("hint_level") or 1)),
            evidence_note=""
            if state.get("evidence_sufficient")
            else (state.get("evidence_note") or ""),
            integrity_policy=deps.integrity_policy,
        )

        request = GenerationRequest(
            system=system,
            user=user,
            question=state.get("student_question") or "",
            evidence=snippets,
            max_output_tokens=1200 if mode == "explain" else 900,
            temperature=0.1 if mode == "assignment" else 0.2,
        )

        try:
            result = deps.llm.generate(request)
        except (ProviderUnavailableError, ConsentRequiredError) as exc:
            log.warning("generation_failed", extra={"reason": str(exc)})
            return {
                "answer": (
                    f"The configured AI provider could not be used: {exc}\n\n"
                    "Your material is still indexed — the sources found for this "
                    "question are listed below."
                ),
                "grounded": False,
                "generative": False,
                "abstained": True,
                "provider": "unavailable",
                "model": "",
                "warnings": ["provider_unavailable"],
            }

        return {
            "answer": result.text,
            "generative": result.generative,
            "provider": result.provider,
            "model": result.model,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "warnings": list(result.warnings),
        }

    return generate


def _make_verify(_deps: GraphDependencies):
    """Resolve and verify citations against the evidence actually supplied."""

    def verify(state: TutorState) -> dict[str, Any]:
        answer = state.get("answer") or ""
        sources = state.get("retrieved_sources") or []
        by_marker = dict(enumerate(sources, start=1))

        referenced = sorted({int(m) for m in _CITATION_RE.findall(answer)})
        valid = [marker for marker in referenced if marker in by_marker]
        invalid = [marker for marker in referenced if marker not in by_marker]

        warnings: list[str] = []
        cleaned = answer
        if invalid:
            # A marker with no matching passage is a fabricated citation. Strip it
            # rather than render a link that goes nowhere.
            for marker in invalid:
                cleaned = cleaned.replace(f"[{marker}]", "")
            warnings.append(
                f"{len(invalid)} citation marker(s) did not match a retrieved passage "
                "and were removed."
            )
            log.warning("invalid_citations_removed", extra={"markers": invalid})

        # If the model cited nothing, attach the retrieved passages anyway so the
        # student can check the answer, but record that it was not cited inline.
        markers_to_attach = valid or list(by_marker)[: min(3, len(by_marker))]
        citations = []
        for marker in markers_to_attach:
            source = by_marker[marker]
            quote = " ".join(str(source.get("text", "")).split())[:320]
            citations.append(
                {
                    "marker": marker,
                    "chunk_id": source.get("chunk_id", ""),
                    "document_id": source.get("document_id", ""),
                    "document_title": source.get("document_title", ""),
                    "page_number": int(source.get("page_number", 0)),
                    "section": source.get("section", ""),
                    "bbox": source.get("bbox"),
                    "score": float(source.get("score", 0.0)),
                    "quote": quote,
                    "cited_inline": marker in valid,
                }
            )

        grounded = bool(valid) and bool(state.get("evidence_sufficient"))
        if not valid and sources:
            warnings.append("The answer did not cite any passage inline.")

        return {
            "answer": cleaned.strip(),
            "citations": citations,
            "grounded": grounded,
            "warnings": warnings,
        }

    return verify


def _make_abstain(_deps: GraphDependencies):
    def abstain(state: TutorState) -> dict[str, Any]:
        note = state.get("evidence_note") or "Nothing in your material matched this question."
        return {
            "answer": (
                f"{note}\n\n"
                "I will not answer from general knowledge, because then I could not show "
                "you where it comes from in your course. Try naming the concept as your "
                "slides do, or upload the lecture that covers it."
            ),
            "citations": [],
            "grounded": False,
            "abstained": True,
            "generative": False,
            "provider": "abstain",
            "model": "",
        }

    return abstain


def _make_recommend(_deps: GraphDependencies):
    """Deterministic next action. Never a model call."""

    def recommend(state: TutorState) -> dict[str, Any]:
        if state.get("abstained"):
            return {
                "recommended_action": "upload_material",
                "recommendation_reason": "This course has no material covering that question.",
            }
        sources = state.get("retrieved_sources") or []
        pages = sorted({int(s.get("page_number", 0)) for s in sources if s.get("page_number")})
        if not state.get("evidence_sufficient"):
            return {
                "recommended_action": "review_source",
                "recommendation_reason": (
                    "The match was weak — read the cited pages yourself before relying on this."
                ),
            }
        if state.get("learning_mode") == "assignment":
            return {
                "recommended_action": "next_hint",
                "recommendation_reason": "Try the step, then ask for the next hint.",
            }
        if pages:
            return {
                "recommended_action": "checkpoint_quiz",
                "recommendation_reason": (
                    f"Test yourself on page{'s' if len(pages) > 1 else ''} "
                    f"{', '.join(str(p) for p in pages[:3])}."
                ),
            }
        return {"recommended_action": "continue_reading", "recommendation_reason": ""}

    return recommend
