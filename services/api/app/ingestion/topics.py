"""Topic-boundary detection.

Deliberately deterministic and explainable: topics come from the document's own
heading structure, with consecutive pages sharing a heading grouped together.
A language model is never required to produce the course outline, so the
structure is identical on every run and is correct even with no model installed.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.ingestion.pdf import ExtractedPage

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_LEADING_NUMBER = re.compile(r"^\s*(?:\d+[.)]?\s*)+")
_NOISE = re.compile(
    r"^(?:slide|page|lecture|chapter|week|section|part)\s*\d*\s*[:.\-–]?\s*", re.IGNORECASE
)


def slugify(value: str) -> str:
    normalised = unicodedata.normalize("NFKD", value)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_STRIP.sub("-", ascii_only).strip("-")
    return slug[:200] or "untitled"


def normalise_heading(heading: str) -> str:
    text = heading.replace("\n", " ").strip()
    text = _NOISE.sub("", text)
    text = _LEADING_NUMBER.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" -–:•")
    return text


@dataclass
class TopicDraft:
    name: str
    slug: str
    first_page: int
    last_page: int
    summary: str = ""


def _is_usable_heading(heading: str) -> bool:
    if len(heading) < 3 or len(heading) > 120:
        return False
    letters = sum(char.isalpha() for char in heading)
    return letters >= 3


def detect_topics(pages: list[ExtractedPage], *, fallback_title: str = "") -> list[TopicDraft]:
    """Group pages into topics using heading continuity.

    Pages with no usable heading inherit the previous topic, which is what
    happens with continuation slides in real lecture decks.
    """
    topics: list[TopicDraft] = []
    current: TopicDraft | None = None

    for page in pages:
        heading = normalise_heading(page.heading)
        if not _is_usable_heading(heading):
            if current is not None:
                current.last_page = page.page_number
            continue

        slug = slugify(heading)
        if current is not None and current.slug == slug:
            current.last_page = page.page_number
            continue

        current = TopicDraft(
            name=heading, slug=slug, first_page=page.page_number, last_page=page.page_number
        )
        topics.append(current)

    if not topics:
        name = normalise_heading(fallback_title) or "Course material"
        last = pages[-1].page_number if pages else 1
        topics = [
            TopicDraft(
                name=name,
                slug=slugify(name),
                first_page=pages[0].page_number if pages else 1,
                last_page=last,
            )
        ]

    # Merge duplicate slugs that reappear later in the deck.
    merged: dict[str, TopicDraft] = {}
    for topic in topics:
        existing = merged.get(topic.slug)
        if existing is None:
            merged[topic.slug] = topic
        else:
            existing.first_page = min(existing.first_page, topic.first_page)
            existing.last_page = max(existing.last_page, topic.last_page)

    result = sorted(merged.values(), key=lambda t: t.first_page)
    page_text = {page.page_number: page.text for page in pages}
    for topic in result:
        snippet = " ".join(
            page_text.get(number, "") for number in range(topic.first_page, topic.last_page + 1)
        )
        topic.summary = " ".join(snippet.split())[:400]
    return result


def topic_slug_for_pages(topics: list[TopicDraft]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for topic in topics:
        for page_number in range(topic.first_page, topic.last_page + 1):
            mapping[page_number] = topic.slug
    return mapping


def section_for_pages(topics: list[TopicDraft], pages: list[ExtractedPage]) -> dict[int, str]:
    by_page = {page.page_number: normalise_heading(page.heading) for page in pages}
    mapping: dict[int, str] = {}
    for topic in topics:
        for page_number in range(topic.first_page, topic.last_page + 1):
            mapping[page_number] = by_page.get(page_number) or topic.name
    return mapping
