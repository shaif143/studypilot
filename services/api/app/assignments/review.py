"""Reviewing a student's attempt at one step of an assignment guide.

The student pastes what they have done — a paragraph of reasoning, a script,
a derivation — and asks whether it is on the right track. The answer names
the reasoning error, cites the lecture passage that shows why, and gives the
next hint. It never completes the step for them: that is the difference
between a coach and a ghostwriter, and it is enforced in the prompt, in the
hint ladder below, and in the deterministic checks on the model's output.

The hint ladder is the blueprint's: each time the student asks about the same
step, the hint gets one rung more specific, decided by code from how many
earlier reviews exist, so the model cannot skip to the end on the first ask:

    1  the concept to think about
    2  the relevant formula, theorem or rule from the material
    3  an outline of the approach
    4  an analogous worked example from the material
    5  a line-by-line review of the attempt's logic

Like the planner, evidence is retrieved from lecture material only and the
model's citation markers are verified; a marker that does not exist is
dropped, never invented into a page reference.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.assignments import planner as p
from app.assignments.prompts import REVIEW_SYSTEM, review_prompt
from app.config import Settings
from app.models.base import GenerationRequest, LLMProvider
from app.rag.retriever import HybridRetriever, RetrievedChunk

log = logging.getLogger(__name__)

MAX_ATTEMPT_CHARS = 12_000
MAX_HINT_LEVEL = 5
VERDICTS = ("on_track", "needs_work", "off_track")
EVIDENCE_TOP_K = 8


class ReviewNeedsModelError(RuntimeError):
    """Raised when the active provider cannot write a review."""


@dataclass
class ReviewIssue:
    what: str
    why: str
    #: Markers into the review's citations, verified by code.
    citations: list[int] = field(default_factory=list)


@dataclass
class AttemptReview:
    verdict: str
    summary: str
    issues: list[ReviewIssue]
    next_hint: str
    hint_level: int
    citations: list[p.Citation]
    provider: str
    model: str
    warnings: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    def to_record(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "summary": self.summary,
            "issues": [asdict(i) for i in self.issues],
            "next_hint": self.next_hint,
            "hint_level": self.hint_level,
            "citations": [asdict(c) for c in self.citations],
            "provider": self.provider,
            "model": self.model,
            "warnings": list(self.warnings),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


def hint_level_for(previous_reviews: int) -> int:
    """One rung higher each time the student comes back to the same step."""
    return max(1, min(MAX_HINT_LEVEL, previous_reviews + 1))


def review_attempt(
    *,
    settings: Settings,
    llm: LLMProvider,
    retriever: HybridRetriever,
    user_id: str,
    course_id: str,
    lecture_document_ids: list[str],
    brief_title: str,
    step: dict[str, Any],
    attempt_text: str,
    previous_reviews: list[dict[str, Any]],
    integrity_policy: str = "",
) -> AttemptReview:
    if not getattr(llm.info, "generative", False):
        raise ReviewNeedsModelError(
            "Checking an attempt needs a language model. Choose one in Settings "
            "(Ollama runs on this Mac; Claude and Gemini need cloud processing on)."
        )
    text = attempt_text.strip()
    if not text:
        raise ValueError("Paste what you have tried so far first.")
    warnings: list[str] = []
    if len(text) > MAX_ATTEMPT_CHARS:
        warnings.append(
            f"Only the first {MAX_ATTEMPT_CHARS:,} characters of the attempt were read."
        )
        text = text[:MAX_ATTEMPT_CHARS]

    level = hint_level_for(len(previous_reviews))
    pool = _evidence_for(
        retriever,
        step,
        text,
        user_id=user_id,
        course_id=course_id,
        lecture_document_ids=lecture_document_ids,
    )
    snippets = p._snippets_for(pool)

    result = llm.generate(
        GenerationRequest(
            system=REVIEW_SYSTEM,
            user=review_prompt(
                brief_title=brief_title,
                step=step,
                attempt_text=text,
                snippets=snippets,
                hint_level=level,
                previous_reviews=previous_reviews,
                integrity_policy=integrity_policy,
            ),
            question=step.get("title", ""),
            evidence=snippets,
            max_output_tokens=2500,
            temperature=0.1,
            timeout_seconds=p._timeout_for(llm, cloud_seconds=180),
        )
    )
    try:
        data = p._parse_json_object(result.text)
    except p._BadModelOutput as exc:
        # Keep what the model said rather than nothing: a prose review is still
        # a review, just without the structure.
        warnings.append(f"The model's review could not be read as structured output ({exc}).")
        data = {"verdict": "needs_work", "summary": result.text.strip()[:4000], "issues": []}

    valid_markers = {s.marker for s in snippets}
    used: set[int] = set()

    def clean(value: Any) -> str:
        text_value = str(value or "").strip()
        for marker in p._CITATION_RE.findall(text_value):
            if int(marker) in valid_markers:
                used.add(int(marker))
        return p._CITATION_RE.sub(
            lambda m: m.group(0) if int(m.group(1)) in valid_markers else "", text_value
        )

    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in VERDICTS:
        verdict = "needs_work"
    issues: list[ReviewIssue] = []
    for raw in data.get("issues") or []:
        if not isinstance(raw, dict):
            continue
        what, why = clean(raw.get("what")), clean(raw.get("why"))
        if not what:
            continue
        markers = sorted(
            {int(m) for m in p._CITATION_RE.findall(f"{what} {why}") if int(m) in valid_markers}
        )
        issues.append(ReviewIssue(what=what, why=why, citations=markers))
    summary = clean(data.get("summary"))
    next_hint = clean(data.get("next_hint"))
    if not summary and not issues:
        summary = "The model returned no readable review. Try again, or ask the tutor."
        warnings.append("Empty review from the model.")

    citations = [
        p.Citation(
            marker=s.marker,
            chunk_id=pool[s.marker - 1].chunk_id,
            document_id=pool[s.marker - 1].document_id,
            document_title=s.document_title,
            page_number=s.page_number,
            section=s.section,
            quote=s.text[:240],
        )
        for s in snippets
        if s.marker in used
    ]
    if not citations and verdict != "on_track" and pool:
        warnings.append(
            "The review does not cite your lecture material; treat its reasons as the "
            "model's own, not your slides'."
        )
    return AttemptReview(
        verdict=verdict,
        summary=summary,
        issues=issues,
        next_hint=next_hint,
        hint_level=level,
        citations=citations,
        provider=getattr(llm.info, "key", "unknown"),
        model=getattr(llm.info, "model", ""),
        warnings=warnings,
        input_tokens=int(getattr(result, "input_tokens", 0) or 0),
        output_tokens=int(getattr(result, "output_tokens", 0) or 0),
    )


_MARKER_STRIP = re.compile(r"\s*\[\d{1,2}\]")


def _evidence_for(
    retriever: HybridRetriever,
    step: dict[str, Any],
    attempt_text: str,
    *,
    user_id: str,
    course_id: str,
    lecture_document_ids: list[str],
) -> list[RetrievedChunk]:
    """Lecture passages for the step's subject and for what the attempt talks about."""
    if not lecture_document_ids:
        return []
    instruction = _MARKER_STRIP.sub("", str(step.get("instruction", "")))
    queries = [
        f"{step.get('title', '')} {instruction}"[:600].strip(),
        attempt_text[:600].strip(),
    ]
    by_id: dict[str, RetrievedChunk] = {}
    for query in queries:
        if not query:
            continue
        outcome = retriever.retrieve(
            query,
            user_id=user_id,
            course_ids=[course_id],
            document_ids=lecture_document_ids,
            top_k=EVIDENCE_TOP_K,
        )
        for chunk in outcome.chunks:
            if chunk.chunk_id not in by_id or chunk.score > by_id[chunk.chunk_id].score:
                by_id[chunk.chunk_id] = chunk
    return sorted(by_id.values(), key=lambda c: c.score, reverse=True)[:EVIDENCE_TOP_K]
