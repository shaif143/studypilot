"""The assignment planner.

    brief ─▶ requirements ─▶ retrieve lecture material per requirement
          ─▶ draft steps ─▶ ground each step ─▶ plan

Deterministic code owns: which documents count as course material, the
retrieval, the grounding decision, the approval state, and the disclosure
text. The model owns only the wording of the requirements and the steps.

The grounding rule
------------------
A step is ``slides`` (grounded) only when all of the following hold:

* the model cited at least one passage marker that exists;
* the model did not itself flag the step as beyond the material;
* the step's content terms are mostly present in the passages it cited —
  the same term-coverage test the tutor uses for sufficiency.

Anything else is ``beyond`` and waits for the student's approval, with a
disclosure saying which terms the lecture material does not cover. A step the
model calls grounded can therefore still be held back; a step it calls beyond
never becomes grounded. The asymmetry is deliberate: the expensive mistake is
telling a student their slides cover something they do not.

Without a generative model the planner still works, honestly: it lists the
brief's requirements, attaches the matching lecture passages to each, and
says that a drafted plan needs a model.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from app.assignments.prompts import (
    PLAN_SYSTEM,
    REQUIREMENTS_SYSTEM,
    plan_prompt,
    requirements_prompt,
)
from app.config import Settings
from app.models.base import (
    ConsentRequiredError,
    EvidenceSnippet,
    GenerationRequest,
    LLMProvider,
    ProviderUnavailableError,
)
from app.rag.retriever import HybridRetriever, RetrievedChunk
from app.rag.sparse import content_terms

log = logging.getLogger(__name__)

_CITATION_RE = re.compile(r"\[(\d{1,2})\]")
#: Lecture passages supplied to the model, in characters. Larger than the
#: tutor's budget because a plan spans the whole brief, not one question.
MAX_EVIDENCE_CHARS = 18_000
MAX_REQUIREMENTS = 14
MAX_BRIEF_CHARS = 24_000
#: Passages fetched per requirement; deduplicated across requirements.
PER_REQUIREMENT_TOP_K = 4

Grounding = str  # "slides" | "beyond"
Approval = str  # "not_required" | "pending" | "approved" | "declined"


@dataclass
class Requirement:
    id: str
    text: str
    kind: str = "deliverable"
    quote: str = ""


@dataclass
class Citation:
    marker: int
    chunk_id: str
    document_id: str
    document_title: str
    page_number: int
    section: str = ""
    quote: str = ""


@dataclass
class PlanStep:
    index: int
    title: str
    instruction: str
    why: str = ""
    requirement_ids: list[str] = field(default_factory=list)
    grounding: Grounding = "beyond"
    citations: list[Citation] = field(default_factory=list)
    approval: Approval = "pending"
    disclosure: str = ""
    #: Fraction of the step's content terms found in its cited passages.
    coverage: float = 0.0
    #: What the model itself said about the step's grounding, kept for audit.
    model_flagged_beyond: bool = False
    beyond_reason: str = ""


@dataclass
class PlanDraft:
    summary: str
    requirements: list[Requirement]
    steps: list[PlanStep]
    provider: str
    model: str
    generative: bool
    status: str  # ready | needs_approval | failed
    warnings: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    def to_record(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "requirements": [asdict(r) for r in self.requirements],
            "steps": [asdict(s) for s in self.steps],
            "provider": self.provider,
            "model": self.model,
            "generative": self.generative,
            "status": self.status,
            "warnings": list(self.warnings),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


# --- public entry point --------------------------------------------------
def build_plan(
    *,
    settings: Settings,
    llm: LLMProvider,
    retriever: HybridRetriever,
    user_id: str,
    course_id: str,
    brief_title: str,
    brief_text: str,
    lecture_document_ids: list[str],
    integrity_policy: str = "",
    release_db: Callable[[], None] | None = None,
) -> PlanDraft:
    """Draft a plan. See the module docstring for the grounding rule.

    ``release_db`` is called right before each model call. A plan takes a
    model a minute or more — a quarter of an hour for an 8B model on a laptop
    — and the caller's database session must not sit on an open read
    transaction for that long: on SQLite it blocks every writer in the
    application, and on PostgreSQL it pins one of five pooled connections.
    The session re-opens a transaction on its next query by itself.
    """
    warnings: list[str] = []
    brief_text = brief_text.strip()
    if len(brief_text) > MAX_BRIEF_CHARS:
        warnings.append(
            f"The brief is long; only its first {MAX_BRIEF_CHARS:,} characters were used."
        )
        brief_text = brief_text[:MAX_BRIEF_CHARS]
    if not brief_text:
        return PlanDraft(
            summary="",
            requirements=[],
            steps=[],
            provider="none",
            model="",
            generative=False,
            status="failed",
            warnings=["No text could be read from this brief, so there is nothing to plan from."],
        )
    if not lecture_document_ids:
        warnings.append(
            "This course has no lecture material yet, so no step can be grounded. "
            "Upload the lectures first for a guide that points to your slides."
        )

    generative = bool(getattr(llm.info, "generative", False))
    tokens = {"in": 0, "out": 0}

    # 1. requirements --------------------------------------------------------
    summary, requirements = "", []
    if generative:
        try:
            if release_db:
                release_db()
            summary, requirements, used = _extract_requirements_with_model(
                llm, brief_title, brief_text
            )
            tokens["in"] += used[0]
            tokens["out"] += used[1]
        except (ProviderUnavailableError, ConsentRequiredError) as exc:
            warnings.append(f"The model could not be used: {exc}")
            generative = False
        except _BadModelOutput as exc:
            warnings.append(f"The model's requirement list could not be read ({exc}).")
            generative = False
    if not requirements:
        summary, requirements = _extract_requirements_heuristically(brief_text)
        if not requirements:
            requirements = [
                Requirement(id="r1", text=summary[:300] or "Complete the assignment as briefed.")
            ]

    # 2. evidence ------------------------------------------------------------
    pool = _gather_evidence(
        retriever,
        requirements,
        user_id=user_id,
        course_id=course_id,
        lecture_document_ids=lecture_document_ids,
    )
    snippets = _snippets_for(pool)

    # 3. steps ---------------------------------------------------------------
    provider_key = getattr(llm.info, "key", "unknown")
    model_id = getattr(llm.info, "model", "")
    raw_steps: list[dict[str, Any]] = []
    if generative:
        try:
            if release_db:
                release_db()
            raw_steps, used = _draft_steps_with_model(
                llm,
                brief_title=brief_title,
                brief_text=brief_text,
                summary=summary,
                requirements=requirements,
                snippets=snippets,
                integrity_policy=integrity_policy,
            )
            tokens["in"] += used[0]
            tokens["out"] += used[1]
            note = raw_steps[0].pop("_note", "") if raw_steps else ""
            if note:
                warnings.append(note)
        except (ProviderUnavailableError, ConsentRequiredError) as exc:
            warnings.append(f"The model could not be used: {exc}")
            generative = False
        except _BadModelOutput as exc:
            warnings.append(f"The model's plan could not be read ({exc}).")
            generative = False
    if not raw_steps:
        raw_steps = _draft_steps_heuristically(requirements, pool)
        if not generative:
            warnings.append(
                "No language model was used: these are the brief's requirements with the "
                "lecture passages that match each one, not a drafted plan. Connect a "
                "model in Settings for step-by-step guidance."
            )

    # 4. grounding -----------------------------------------------------------
    steps = _ground_steps(raw_steps, pool, settings)
    status = "needs_approval" if any(s.approval == "pending" for s in steps) else "ready"

    return PlanDraft(
        summary=summary,
        requirements=requirements,
        steps=steps,
        provider=provider_key if generative else "extractive",
        model=model_id if generative else "",
        generative=generative,
        status=status,
        warnings=warnings,
        input_tokens=tokens["in"],
        output_tokens=tokens["out"],
    )


# --- requirements ---------------------------------------------------------
class _BadModelOutput(ValueError):
    pass


def _timeout_for(llm: LLMProvider, cloud_seconds: float) -> float:
    """On-device models get much longer: an 8B model on a laptop needs a few
    minutes for a plan, and the only cost of waiting is time. A cloud call
    that has not answered in a minute or two is not going to."""
    return cloud_seconds if getattr(llm.info, "sends_data_off_device", True) else 900.0


def _extract_requirements_with_model(
    llm: LLMProvider, brief_title: str, brief_text: str
) -> tuple[str, list[Requirement], tuple[int, int]]:
    result = llm.generate(
        GenerationRequest(
            system=REQUIREMENTS_SYSTEM,
            user=requirements_prompt(brief_title, brief_text),
            question=brief_title,
            # A dozen requirements with quotes runs past 1,800 tokens on a
            # verbose model; Gemini's first real list was cut off there.
            max_output_tokens=4000,
            temperature=0.0,
            json_schema={"type": "object"},
            timeout_seconds=_timeout_for(llm, 120.0),
        )
    )
    try:
        data = _parse_json_object(result.text)
        items = data.get("requirements")
    except _BadModelOutput:
        # Cut off mid-list: keep the complete entries, lose the summary.
        items = _salvage_objects(result.text, ("id", "text"))
        data = {"summary": ""}
        if not items:
            raise
    if not isinstance(items, list) or not items:
        raise _BadModelOutput("no requirements array")
    requirements: list[Requirement] = []
    for position, item in enumerate(items[:MAX_REQUIREMENTS], start=1):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        kind = str(item.get("kind", "deliverable")).strip().lower()
        if kind not in {"deliverable", "constraint", "criterion", "tool"}:
            kind = "deliverable"
        requirements.append(
            Requirement(
                id=str(item.get("id") or f"r{position}").strip()[:8],
                text=text[:400],
                kind=kind,
                quote=str(item.get("quote", "")).strip()[:400],
            )
        )
    if not requirements:
        raise _BadModelOutput("requirements were empty")
    summary = str(data.get("summary", "")).strip()[:1200]
    return summary, requirements, (result.input_tokens or 0, result.output_tokens or 0)


_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)]|[a-z][.)]|\(\d+\))\s+(.*\S)\s*$")
_REQUIREMENT_WORDS = re.compile(
    r"\b(must|should|submit|deliver|write|implement|produce|report|plot|use|include|"
    r"required|requirement|marks?|word limit|deadline|due)\b",
    re.I,
)


def _extract_requirements_heuristically(brief_text: str) -> tuple[str, list[Requirement]]:
    """Bulleted and numbered lines, then sentences that read like instructions."""
    lines = [line.strip() for line in brief_text.splitlines()]
    candidates: list[str] = []
    for line in lines:
        match = _BULLET_RE.match(line)
        if match and len(match.group(1)) > 12:
            candidates.append(match.group(1))
    if len(candidates) < 3:
        for raw_sentence in re.split(r"(?<=[.!?])\s+", " ".join(lines)):
            sentence = raw_sentence.strip()
            if 20 < len(sentence) < 400 and _REQUIREMENT_WORDS.search(sentence):
                candidates.append(sentence)
    seen: set[str] = set()
    requirements: list[Requirement] = []
    for text in candidates:
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        kind = (
            "tool"
            if re.search(r"\b(matlab|python|r|octave|simulink|excel)\b", text, re.I)
            else "deliverable"
        )
        requirements.append(
            Requirement(
                id=f"r{len(requirements) + 1}", text=text[:400], kind=kind, quote=text[:400]
            )
        )
        if len(requirements) >= MAX_REQUIREMENTS:
            break
    summary = " ".join(lines)[:600]
    return summary, requirements


# --- evidence -------------------------------------------------------------
def _gather_evidence(
    retriever: HybridRetriever,
    requirements: list[Requirement],
    *,
    user_id: str,
    course_id: str,
    lecture_document_ids: list[str],
) -> list[RetrievedChunk]:
    """One retrieval per requirement, merged and deduplicated by chunk.

    Scoped to the course's lecture material only: the brief itself and other
    assignment or past-paper documents are excluded, because a plan that
    cites the brief as evidence that the slides cover something is circular.
    """
    if not lecture_document_ids:
        return []
    pool: dict[str, RetrievedChunk] = {}
    for requirement in requirements:
        outcome = retriever.retrieve(
            requirement.text,
            user_id=user_id,
            course_ids=[course_id],
            document_ids=lecture_document_ids,
            top_k=PER_REQUIREMENT_TOP_K,
        )
        for chunk in outcome.chunks:
            if chunk.chunk_id not in pool or chunk.score > pool[chunk.chunk_id].score:
                pool[chunk.chunk_id] = chunk
    return sorted(pool.values(), key=lambda c: c.score, reverse=True)


def _snippets_for(pool: list[RetrievedChunk]) -> list[EvidenceSnippet]:
    snippets: list[EvidenceSnippet] = []
    budget = MAX_EVIDENCE_CHARS
    for marker, chunk in enumerate(pool, start=1):
        text = (chunk.parent_text or chunk.text)[: max(400, budget)]
        budget -= len(text)
        snippets.append(
            EvidenceSnippet(
                marker=marker,
                text=text,
                page_number=chunk.page_number,
                document_title=chunk.document_title,
                section=chunk.section,
            )
        )
        if budget <= 0:
            break
    return snippets


# --- steps ----------------------------------------------------------------
def _draft_steps_with_model(
    llm: LLMProvider,
    *,
    brief_title: str,
    brief_text: str,
    summary: str,
    requirements: list[Requirement],
    snippets: list[EvidenceSnippet],
    integrity_policy: str,
) -> tuple[list[dict[str, Any]], tuple[int, int]]:
    result = llm.generate(
        GenerationRequest(
            system=PLAN_SYSTEM,
            user=plan_prompt(
                brief_title=brief_title,
                brief_text=brief_text,
                summary=summary,
                requirements=[asdict(r) for r in requirements],
                snippets=snippets,
                integrity_policy=integrity_policy,
            ),
            question=brief_title,
            evidence=snippets,
            # A plan for a dozen requirements runs to several thousand tokens;
            # the first real run was cut off at 3,500 and the JSON would not
            # parse. The budget is generous and a cut-off is salvaged below.
            max_output_tokens=8000,
            temperature=0.1,
            json_schema={"type": "object"},
            timeout_seconds=_timeout_for(llm, 300.0),
        )
    )
    salvage_note = ""
    try:
        data = _parse_json_object(result.text)
        steps = data.get("steps")
    except _BadModelOutput:
        # Usually a response cut off mid-array. Complete step objects are
        # still worth having; the plan says that it was cut short.
        steps = _salvage_objects(result.text, ("title", "instruction"))
        if not steps:
            raise
        salvage_note = (
            f"The model's plan was cut short; {len(steps)} complete step(s) were recovered "
            "and the rest were dropped. Draft again for a full plan."
        )
    if not isinstance(steps, list) or not steps:
        raise _BadModelOutput("no steps array")
    cleaned: list[dict[str, Any]] = []
    for item in steps[:16]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        instruction = str(item.get("instruction", "")).strip()
        if not title or not instruction:
            continue
        cleaned.append(
            {
                "title": title[:160],
                "instruction": instruction[:1600],
                "why": str(item.get("why", "")).strip()[:600],
                "requirement_ids": [
                    str(r)[:8] for r in (item.get("requirement_ids") or []) if str(r).strip()
                ][:6],
                "beyond_slides": bool(item.get("beyond_slides", False)),
                "beyond_reason": str(item.get("beyond_reason", "")).strip()[:400],
            }
        )
    if not cleaned:
        raise _BadModelOutput("steps were empty")
    if salvage_note:
        cleaned[0] = {**cleaned[0], "_note": salvage_note}
    return cleaned, (result.input_tokens or 0, result.output_tokens or 0)


def _draft_steps_heuristically(
    requirements: list[Requirement], pool: list[RetrievedChunk]
) -> list[dict[str, Any]]:
    """One step per requirement, citing the passages that best match it.

    No model: the instruction is the requirement itself, and the 'how' is a
    pointer to the passages, which is the most this can honestly offer.
    """
    steps: list[dict[str, Any]] = []
    for requirement in requirements:
        terms = content_terms(requirement.text)
        ranked = sorted(
            enumerate(pool, start=1),
            key=lambda pair: -sum(1 for t in terms if _term_in(t, pair[1].text)),
        )
        markers = [m for m, chunk in ranked[:3] if any(_term_in(t, chunk.text) for t in terms)]
        cite = " ".join(f"[{m}]" for m in markers)
        steps.append(
            {
                "title": requirement.text[:80],
                "instruction": (
                    f"Address this requirement: {requirement.text} "
                    + (f"Your lecture material covers it here: {cite}." if cite else "")
                ).strip(),
                "why": f"Required by the brief ({requirement.kind}).",
                "requirement_ids": [requirement.id],
                "beyond_slides": not markers,
                "beyond_reason": "" if markers else "No lecture passage matched this requirement.",
            }
        )
    return steps


# --- grounding ------------------------------------------------------------
def _term_in(term: str, text: str) -> bool:
    stem = term[:-1] if len(term) > 4 and term.endswith("s") else term
    return stem in text.lower()


def _ground_steps(
    raw_steps: list[dict[str, Any]], pool: list[RetrievedChunk], settings: Settings
) -> list[PlanStep]:
    by_marker = dict(enumerate(pool, start=1))
    steps: list[PlanStep] = []
    for index, raw in enumerate(raw_steps, start=1):
        text = f"{raw['title']} {raw['instruction']}"
        referenced = sorted({int(m) for m in _CITATION_RE.findall(text)})
        # Coverage is judged on the step's own words. The heuristic plan wraps
        # each requirement in template phrases; those are ours, not the
        # brief's, and must not show up as "terms your slides do not cover".
        wording = _TEMPLATE_RE.sub(" ", text)
        valid = [m for m in referenced if m in by_marker]
        invalid = [m for m in referenced if m not in by_marker]
        instruction = raw["instruction"]
        for marker in invalid:
            instruction = instruction.replace(f"[{marker}]", "")

        cited_chunks = [by_marker[m] for m in valid]
        terms = content_terms(wording)
        coverage = (
            sum(1 for t in terms if any(_term_in(t, c.text) for c in cited_chunks)) / len(terms)
            if terms and cited_chunks
            else 0.0
        )
        missing = [t for t in terms if not any(_term_in(t, c.text) for c in cited_chunks)]

        model_beyond = bool(raw.get("beyond_slides"))
        grounded = (
            bool(valid) and not model_beyond and coverage >= settings.sufficiency_min_coverage
        )

        citations = [
            Citation(
                marker=m,
                chunk_id=by_marker[m].chunk_id,
                document_id=by_marker[m].document_id,
                document_title=by_marker[m].document_title,
                page_number=by_marker[m].page_number,
                section=by_marker[m].section,
                quote=" ".join(by_marker[m].text.split())[:280],
            )
            for m in valid
        ]

        disclosure = ""
        if not grounded:
            disclosure = _disclosure_for(
                title=raw["title"],
                missing_terms=_content_only(missing if cited_chunks else terms)[:4],
                model_reason=raw.get("beyond_reason", ""),
                had_citations=bool(valid),
                model_flagged=model_beyond,
            )

        steps.append(
            PlanStep(
                index=index,
                title=raw["title"],
                instruction=instruction.strip(),
                why=raw.get("why", ""),
                requirement_ids=list(raw.get("requirement_ids") or []),
                grounding="slides" if grounded else "beyond",
                citations=citations,
                approval="not_required" if grounded else "pending",
                disclosure=disclosure,
                coverage=round(coverage, 3),
                model_flagged_beyond=model_beyond,
                beyond_reason=str(raw.get("beyond_reason", "")),
            )
        )
    return steps


#: Words about doing the work rather than the subject. Their absence from
#: the slides means nothing, so they never appear in a disclosure.
_META_WORDS = frozenset(
    [
        "address",
        "add",
        "apply",
        "brief",
        "calculate",
        "calculation",
        "calculations",
        "check",
        "choose",
        "compare",
        "confirm",
        "decide",
        "deliverable",
        "deliverables",
        "document",
        "environment",
        "find",
        "hand",
        "include",
        "item",
        "items",
        "keep",
        "list",
        "log",
        "map",
        "note",
        "organise",
        "package",
        "place",
        "plan",
        "put",
        "record",
        "report",
        "requirement",
        "requirements",
        "review",
        "run",
        "script",
        "section",
        "sections",
        "set",
        "state",
        "step",
        "steps",
        "submit",
        "task",
        "tool",
        "tools",
        "use",
        "verify",
        "word",
        "words",
        "work",
        "write",
    ]
)


def _content_only(terms: list[str]) -> list[str]:
    return [t for t in terms if t not in _META_WORDS and not t.isdigit()]


def _disclosure_for(
    *,
    title: str,
    missing_terms: list[str],
    model_reason: str,
    had_citations: bool,
    model_flagged: bool = False,
) -> str:
    """The warning shown on a step that goes beyond the lecture material.

    Written by code, not the model, so it says the same thing every time and
    cannot be softened. Three situations read differently:

    * the model itself said the step needs something the material lacks —
      its reason is quoted after the fixed lead;
    * the step cites nothing — it rests on knowledge from outside the slides;
    * the step cites the slides but its wording mostly does not match them —
      either a real gap or an organisational step the course never needed to
      teach; the student can tell which, and is told to.
    """
    what = ", ".join(missing_terms) if missing_terms else title.lower()
    reason = f" {model_reason.strip().rstrip('.')}." if model_reason.strip() else ""
    if model_flagged or not had_citations:
        lead = (
            f"If you follow this step you will be using {what}, which is not in your "
            "lecture slides for this course."
        )
    else:
        lead = (
            f"This step cites your slides, but they do not cover {what}. That may be a "
            "real gap, or a step about organising the work that your lectures never "
            "needed to teach."
        )
    return (
        f"{lead}{reason} Check whether your assignment allows methods beyond the taught "
        "material before approving it."
    )


_TEMPLATE_RE = re.compile(
    r"Address this requirement:|Your lecture material covers it here:|\[\d{1,2}\]", re.I
)


def _salvage_objects(text: str, required: tuple[str, ...]) -> list[dict[str, Any]]:
    """Recover complete `{...}` objects carrying all of ``required`` keys.

    Scans with a stack of object starts rather than a regex, so nested
    objects and strings containing braces do not break it. The innermost
    object closes first; the outer `{"steps": [...]` of a truncated response
    never closes and is simply never tried.
    """
    objects: list[dict[str, Any]] = []
    starts: list[int] = []
    in_string = False
    escape = False
    for position, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            starts.append(position)
        elif char == "}" and starts:
            begin = starts.pop()
            try:
                candidate = json.loads(text[begin : position + 1])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and all(candidate.get(key) for key in required):
                objects.append(candidate)
    return objects


# --- json -----------------------------------------------------------------
def _parse_json_object(text: str) -> dict[str, Any]:
    """Read a JSON object out of model output, tolerating fences and preamble."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z]*\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise _BadModelOutput("no JSON object in the output")
    try:
        data = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError as exc:
        raise _BadModelOutput(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise _BadModelOutput("JSON was not an object")
    return data
