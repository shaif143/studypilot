"""Personalised note generation.

Three modes, each assembled from different inputs:

``concept``   the material's own structure — topics, headings, key passages
``exam``      the same material reorganised around what is likely to be assessed
``weakness``  driven by the student's mastery estimates and marked mistakes

With a generative provider the notes are written; without one they are
assembled deterministically from the student's own material, with page
citations, and labelled as an outline rather than written notes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.agents.prompts import _INJECTION_GUARD
from app.agents.service import resolve_provider
from app.learning.mastery import WEAK_THRESHOLD
from app.models.base import GenerationRequest, ProviderUnavailableError
from app.runtime import RuntimeContext
from app.storage.models import UserSettings
from app.storage.repositories import (
    AssessmentRepository,
    ChunkRepository,
    CourseRepository,
    MasteryRepository,
    NoteRepository,
    TopicRepository,
)

log = logging.getLogger(__name__)

MODE_TITLES = {
    "concept": "Concept notes",
    "exam": "Exam-focused notes",
    "weakness": "Weak-topic notes",
}


class NoMaterialError(ValueError):
    pass


@dataclass
class NoteDraft:
    title: str
    body_markdown: str
    generated_by: str
    citations: list[dict[str, Any]]
    warnings: list[str]


def _collect(
    *,
    db_session: Any,
    user_id: str,
    course_id: str,
    document_id: str | None,
    topic_slug: str | None,
) -> tuple[list[Any], list[Any]]:
    chunk_repo = ChunkRepository(db_session)
    topic_repo = TopicRepository(db_session)
    CourseRepository(db_session).get(user_id, course_id)

    if document_id:
        chunks = chunk_repo.by_document(user_id, document_id, level="parent")
    else:
        ids = chunk_repo.scoped_ids(user_id, course_ids=[course_id], level="parent")
        chunks = chunk_repo.get_many(user_id, ids)
    if topic_slug:
        chunks = [chunk for chunk in chunks if chunk.topic_slug == topic_slug]
    chunks.sort(key=lambda chunk: (chunk.document_id, chunk.page_number))

    topics = topic_repo.list(user_id, course_id)
    if topic_slug:
        topics = [topic for topic in topics if topic.slug == topic_slug]
    return chunks, topics


def generate_note(
    *,
    context: RuntimeContext,
    db_session: Any,
    user_id: str,
    course_id: str,
    document_id: str | None,
    mode: str,
    topic_slug: str | None,
    title: str | None,
    allow_cloud: bool,
    user_settings: UserSettings,
) -> NoteDraft:
    chunks, topics = _collect(
        db_session=db_session,
        user_id=user_id,
        course_id=course_id,
        document_id=document_id,
        topic_slug=topic_slug,
    )
    if not chunks:
        raise NoMaterialError("There is no indexed material for that selection yet.")

    mastery_repo = MasteryRepository(db_session)
    estimates = mastery_repo.list(user_id, course_id)
    weak = [e for e in estimates if float(e.probability) < WEAK_THRESHOLD and e.observations > 0]

    mistakes: list[str] = []
    for attempt in AssessmentRepository(db_session).attempts_for(user_id, limit=5):
        mistakes.extend((attempt.feedback or {}).get("weak_topics", []))

    if mode == "weakness":
        weak_slugs = {e.topic_slug for e in weak} | set(mistakes)
        if weak_slugs:
            focused = [chunk for chunk in chunks if chunk.topic_slug in weak_slugs]
            if focused:
                chunks = focused
                topics = [topic for topic in topics if topic.slug in weak_slugs] or topics

    # Keep the context affordable; parent chunks are page-sized.
    selected = chunks[:30]
    citations = [
        {
            "marker": index,
            "chunk_id": chunk.id,
            "document_id": chunk.document_id,
            "page_number": chunk.page_number,
            "section": chunk.section,
        }
        for index, chunk in enumerate(selected, start=1)
    ]

    warnings: list[str] = []
    provider, provider_warnings = resolve_provider(
        context.settings, user_settings, requested=None, allow_cloud_request=allow_cloud
    )
    warnings.extend(provider_warnings)

    resolved_title = title or f"{MODE_TITLES.get(mode, 'Notes')}"

    if provider.info.generative:
        body = _write_with_model(
            provider=provider,
            mode=mode,
            chunks=selected,
            topics=topics,
            weak=weak,
            mistakes=sorted(set(mistakes)),
        )
        if body:
            return NoteDraft(
                title=resolved_title,
                body_markdown=body,
                generated_by=f"llm:{provider.info.key}",
                citations=citations,
                warnings=warnings,
            )
        warnings.append("The model did not return usable notes; an outline was built instead.")

    warnings.append(
        "These are an outline assembled from your own material with page references, "
        "not written notes. Configure an AI model for prose notes."
    )
    return NoteDraft(
        title=resolved_title,
        body_markdown=_build_outline(mode, selected, topics, weak, sorted(set(mistakes))),
        generated_by="source_outline",
        citations=citations,
        warnings=warnings,
    )


_MODE_INSTRUCTIONS = {
    "concept": (
        "Write concept notes: for each topic, the definition, why it matters, the key "
        "relationships, and any equation with its symbols explained."
    ),
    "exam": (
        "Write exam-focused notes: the facts and formulas most likely to be examined, "
        "common pitfalls, and a one-line 'how to answer' for each topic."
    ),
    "weakness": (
        "Write remedial notes aimed squarely at the student's weak topics listed below. "
        "Start from the prerequisite idea, then build up. Be concrete."
    ),
}


def _write_with_model(
    *,
    provider: Any,
    mode: str,
    chunks: list[Any],
    topics: list[Any],
    weak: list[Any],
    mistakes: list[str],
) -> str:
    material = "\n\n".join(
        f"[{index}] page {chunk.page_number} — {chunk.section or 'n/a'}\n"
        f"<<<\n{chunk.text[:2200]}\n>>>"
        for index, chunk in enumerate(chunks, start=1)
    )
    context_lines = [f"Topics: {', '.join(topic.name for topic in topics[:20]) or 'n/a'}"]
    if weak:
        context_lines.append(
            "Weak topics (mastery, observations): "
            + ", ".join(
                f"{e.topic_name} ({float(e.probability):.0%}, n={e.observations})"
                for e in weak[:10]
            )
        )
    if mistakes:
        context_lines.append(f"Topics missed in recent assessments: {', '.join(mistakes[:10])}")

    system = (
        "You write revision notes from a student's own course material.\n\n"
        f"{_INJECTION_GUARD}\n\n"
        "Rules:\n"
        "- Use only the supplied passages. Cite them inline as [n].\n"
        "- Never cite a number not in the list.\n"
        "- If you add anything beyond the material, prefix that line with "
        "'Beyond your material:'.\n"
        "- Output GitHub-flavoured Markdown with ## headings. Mathematics in $...$.\n"
        f"- {_MODE_INSTRUCTIONS.get(mode, _MODE_INSTRUCTIONS['concept'])}"
    )
    user = f"{chr(10).join(context_lines)}\n\nMaterial:\n\n{material}"
    try:
        result = provider.generate(
            GenerationRequest(system=system, user=user, max_output_tokens=2600, temperature=0.3)
        )
    except ProviderUnavailableError as exc:
        log.warning("note_generation_failed", extra={"reason": str(exc)})
        return ""
    return result.text.strip()


def _build_outline(
    mode: str, chunks: list[Any], topics: list[Any], weak: list[Any], mistakes: list[str]
) -> str:
    """Deterministic outline: real structure and real page references, no prose."""
    lines: list[str] = [f"# {MODE_TITLES.get(mode, 'Notes')}", ""]
    lines.append("> Assembled from your own material. Each bullet links to the page it came from.")
    lines.append("")

    if mode == "weakness":
        if weak:
            lines.append("## Where you are weakest")
            lines.append("")
            for estimate in weak[:10]:
                lines.append(
                    f"- **{estimate.topic_name}** — {float(estimate.probability):.0%} "
                    f"estimated mastery from {estimate.observations} answer"
                    f"{'s' if estimate.observations != 1 else ''}"
                    + ("" if estimate.observations >= 3 else " (too few answers to be sure)")
                )
            lines.append("")
        if mistakes:
            lines.append(f"Topics missed in recent assessments: {', '.join(mistakes)}.")
            lines.append("")

    by_topic: dict[str, list[Any]] = {}
    for chunk in chunks:
        by_topic.setdefault(chunk.topic_slug or "other", []).append(chunk)

    names = {topic.slug: topic.name for topic in topics}
    marker = 0
    for slug, group in by_topic.items():
        lines.append(f"## {names.get(slug, slug.replace('-', ' ').title())}")
        lines.append("")
        pages = sorted({chunk.page_number for chunk in group})
        lines.append(f"_Pages {pages[0]}–{pages[-1]}_" if len(pages) > 1 else f"_Page {pages[0]}_")
        lines.append("")
        for chunk in group[:6]:
            marker += 1
            snippet = " ".join(chunk.text.split())[:360]
            lines.append(f"- {snippet} [{marker}]")
        lines.append("")

    if mode == "exam":
        lines.append("## Checklist")
        lines.append("")
        for slug in by_topic:
            lines.append(f"- [ ] Can explain **{names.get(slug, slug)}** without the slides")
        lines.append("")

    return "\n".join(lines)


def persist_note(
    *,
    db_session: Any,
    user_id: str,
    course_id: str,
    document_id: str | None,
    mode: str,
    draft: NoteDraft,
) -> Any:
    return NoteRepository(db_session).create(
        user_id=user_id,
        course_id=course_id,
        document_id=document_id,
        title=draft.title,
        mode=mode,
        body_markdown=draft.body_markdown,
        generated_by=draft.generated_by,
        citations=draft.citations,
    )
