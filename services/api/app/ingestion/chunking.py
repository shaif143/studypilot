"""Parent/child chunking.

Child chunks are small and are what gets embedded and matched.  Parent chunks
are page-level and are what gets handed to the model as context, so a precise
match still arrives with enough surrounding material to be explainable.  Every
chunk keeps the page number, section heading and bounding box of what it came
from, which is what makes a citation clickable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.ingestion.pdf import ExtractedPage

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


@dataclass
class ChunkDraft:
    """A chunk before it is given a database identity."""

    level: str
    text: str
    page_number: int
    page_end: int
    section: str
    element_type: str
    bbox: list[float] | None
    ordinal: int
    topic_slug: str = ""
    parent_ordinal: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def token_estimate(self) -> int:
        # ~4 characters per token is close enough for budgeting.
        return max(1, len(self.text) // 4)


def _union_bbox(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def _split_text(text: str, target: int, overlap: int) -> list[str]:
    """Split on sentence boundaries, packing up to ``target`` characters."""
    text = text.strip()
    if len(text) <= target:
        return [text] if text else []

    sentences = [s.strip() for s in _SENTENCE_BOUNDARY.split(text) if s.strip()]
    # A slide with no sentence punctuation (bullet lists) splits on newlines.
    if len(sentences) <= 1:
        sentences = [s.strip() for s in text.splitlines() if s.strip()] or [text]

    pieces: list[str] = []
    current: list[str] = []
    length = 0
    for sentence in sentences:
        if length + len(sentence) > target and current:
            pieces.append(" ".join(current))
            if overlap > 0:
                # Carry the tail of the previous piece forward for context.
                tail: list[str] = []
                tail_length = 0
                for previous in reversed(current):
                    if tail_length + len(previous) > overlap:
                        break
                    tail.insert(0, previous)
                    tail_length += len(previous)
                current = tail
                length = tail_length
            else:
                current = []
                length = 0
        if len(sentence) > target:
            # A single very long sentence is hard-split rather than dropped.
            for start in range(0, len(sentence), target):
                pieces.append(sentence[start : start + target])
            continue
        current.append(sentence)
        length += len(sentence) + 1
    if current:
        pieces.append(" ".join(current))
    return [piece for piece in pieces if piece.strip()]


def build_chunks(
    pages: list[ExtractedPage],
    *,
    child_chars: int = 900,
    child_overlap: int = 150,
    section_for_page: dict[int, str] | None = None,
    topic_for_page: dict[int, str] | None = None,
) -> list[ChunkDraft]:
    """Produce parent and child chunks for an extracted document."""
    section_for_page = section_for_page or {}
    topic_for_page = topic_for_page or {}
    chunks: list[ChunkDraft] = []
    ordinal = 0

    for page in pages:
        if not page.text.strip():
            continue
        section = section_for_page.get(page.page_number) or page.heading
        topic_slug = topic_for_page.get(page.page_number, "")

        parent_ordinal = ordinal
        chunks.append(
            ChunkDraft(
                level="parent",
                text=page.text,
                page_number=page.page_number,
                page_end=page.page_number,
                section=section,
                element_type="text",
                bbox=_union_bbox([e.bbox for e in page.elements]),
                ordinal=ordinal,
                topic_slug=topic_slug,
            )
        )
        ordinal += 1

        # Tables and equations become their own child chunks so that a question
        # about a formula can match the formula rather than the prose near it.
        structural = [e for e in page.elements if e.kind in {"table", "equation"}]
        for element in structural:
            chunks.append(
                ChunkDraft(
                    level="child",
                    text=element.text,
                    page_number=page.page_number,
                    page_end=page.page_number,
                    section=section,
                    element_type=element.kind,
                    bbox=element.bbox,
                    ordinal=ordinal,
                    topic_slug=topic_slug,
                    parent_ordinal=parent_ordinal,
                )
            )
            ordinal += 1

        prose_elements = [e for e in page.elements if e.kind in {"heading", "text"}]
        prose = "\n".join(e.text for e in prose_elements).strip()
        if prose:
            prose_bbox = _union_bbox([e.bbox for e in prose_elements])
            for piece in _split_text(prose, child_chars, child_overlap):
                # Prefix the section so a short bullet still carries its context
                # into the embedding.
                body = f"{section}\n{piece}" if section and section not in piece else piece
                chunks.append(
                    ChunkDraft(
                        level="child",
                        text=body,
                        page_number=page.page_number,
                        page_end=page.page_number,
                        section=section,
                        element_type="text",
                        bbox=prose_bbox,
                        ordinal=ordinal,
                        topic_slug=topic_slug,
                        parent_ordinal=parent_ordinal,
                    )
                )
                ordinal += 1

    return chunks
