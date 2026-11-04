"""PDF structural extraction with PyMuPDF.

This is a deterministic service, not an agent.  It extracts text blocks with
their bounding boxes in PDF points, classifies each block (heading, body,
table-like, equation-like, figure), renders a page preview, and reports what it
could *not* extract so the rest of the system never silently treats an
image-only slide as empty.

Co-ordinates are preserved because the reader highlights cited passages
directly on the rendered page.
"""

from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf

log = logging.getLogger(__name__)

#: Characters that strongly suggest mathematical content.
_MATH_CHARS = set("∑∫∂√±×÷≤≥≠≈∞αβγδθλμπσφωΔΩ∇⊗⟨⟩")
#: Notation that marks a line as mathematical even in plain ASCII exports:
#: LaTeX macros, sub/superscripts, and index notation such as ``x[i,j]``.
_MATH_NOTATION = re.compile(
    r"\\frac|\\sum|\\int|\\partial|[_^]\{|\b[A-Za-z]_[A-Za-z0-9]"
    r"|\[[ijkmn0-9]\s*[,+\-\]]|\^\d"
)
_RELATION = re.compile(r"[=≈≡<>≤≥]")
_WHITESPACE = re.compile(r"[ \t]+")


class UnreadableDocumentError(ValueError):
    """Raised when a file is not a readable PDF at all."""


@dataclass
class PageElement:
    """One extracted block with its position on the page."""

    index: int
    kind: str  # heading | text | table | equation | figure
    text: str
    bbox: list[float]  # [x0, y0, x1, y1] in PDF points
    font_size: float = 0.0
    bold: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "kind": self.kind,
            "text": self.text,
            "bbox": self.bbox,
            "font_size": round(self.font_size, 2),
            "bold": self.bold,
        }


@dataclass
class ExtractedPage:
    page_number: int
    width: float
    height: float
    text: str
    heading: str
    elements: list[PageElement] = field(default_factory=list)
    has_equations: bool = False
    has_figures: bool = False
    has_tables: bool = False
    preview_filename: str = ""
    #: Set when the page yielded no usable text (e.g. a scanned slide).
    warning: str = ""


@dataclass
class ExtractedDocument:
    page_count: int
    pages: list[ExtractedPage]
    title: str = ""
    #: Non-fatal problems: pages with no text, failed previews, encryption notes.
    warnings: list[str] = field(default_factory=list)
    #: True when at least one page produced usable text.
    partial: bool = False

    @property
    def text_pages(self) -> int:
        return sum(1 for page in self.pages if page.text.strip())


def _clean(text: str) -> str:
    text = _WHITESPACE.sub(" ", text.replace("­", ""))
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _looks_like_equation(text: str) -> bool:
    """Heuristic classifier for a mathematical line.

    Deliberately conservative: a paragraph that happens to mention "x = 3" is
    prose, so a relation alone is not enough — the line must also be short and
    carry either real mathematical glyphs, recognisable notation, or a high
    density of operators relative to words.
    """
    stripped = text.strip()
    if not stripped or len(stripped) > 300:
        return False
    if any(char in _MATH_CHARS for char in stripped):
        return True
    if not _RELATION.search(stripped) and not _MATH_NOTATION.search(stripped):
        return False

    has_relation = bool(_RELATION.search(stripped))
    if has_relation and _MATH_NOTATION.search(stripped):
        return True

    # Fall back to symbol density: equations are operator-heavy and word-light.
    operators = sum(1 for char in stripped if char in "=+-*/^_()[]{}<>|")
    letters = sum(1 for char in stripped if char.isalpha())
    density = operators / max(1, operators + letters)
    words = len(stripped.split())
    return has_relation and density >= 0.12 and words <= 24


def _looks_like_table(block_lines: list[str]) -> bool:
    if len(block_lines) < 2:
        return False
    # Tables extracted as text tend to have several runs of 2+ spaces per line.
    gapped = sum(1 for line in block_lines if len(re.findall(r"\s{2,}", line)) >= 2)
    return gapped >= max(2, len(block_lines) // 2)


def _block_spans(block: dict[str, Any]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for line in block.get("lines", []):
        spans.extend(line.get("spans", []))
    return spans


def extract_pdf(
    path: Path, *, max_pages: int, preview_dir: Path | None = None, preview_dpi: int = 110
) -> ExtractedDocument:
    """Extract structure from a PDF file.

    Raises :class:`UnreadableDocumentError` only when the file cannot be opened
    as a PDF at all.  Every other problem is recorded as a warning so a partly
    extractable document still becomes usable.
    """
    try:
        document = pymupdf.open(path)
    except Exception as exc:
        raise UnreadableDocumentError(f"Not a readable PDF: {exc}") from exc

    warnings: list[str] = []
    try:
        if document.needs_pass:
            raise UnreadableDocumentError(
                "This PDF is password protected. Remove the password and upload it again."
            )

        total = document.page_count
        if total == 0:
            raise UnreadableDocumentError("This PDF has no pages.")
        limit = min(total, max_pages)
        if total > limit:
            warnings.append(f"Only the first {limit} of {total} pages were processed (page limit).")

        pages: list[ExtractedPage] = []
        for page_index in range(limit):
            pages.append(_extract_page(document, page_index, preview_dir, preview_dpi, warnings))

        raw_title = (document.metadata or {}).get("title") or ""
        title = raw_title.strip()
        if not title:
            first_heading = next((p.heading for p in pages if p.heading), "")
            title = first_heading.strip()

        extracted = ExtractedDocument(page_count=limit, pages=pages, title=title, warnings=warnings)
        if extracted.text_pages == 0:
            extracted.warnings.append(
                "No text could be extracted from this document. It is probably a scan "
                "or an image-only export, so search and explanations will not work "
                "until a text-based version is uploaded."
            )
        extracted.partial = 0 < extracted.text_pages < limit
        return extracted
    finally:
        document.close()


def _extract_page(
    document: pymupdf.Document,
    page_index: int,
    preview_dir: Path | None,
    preview_dpi: int,
    warnings: list[str],
) -> ExtractedPage:
    page = document[page_index]
    page_number = page_index + 1
    rect = page.rect

    try:
        raw = page.get_text("dict")
    except Exception as exc:  # pragma: no cover - malformed page
        warnings.append(f"Page {page_number}: text extraction failed ({exc}).")
        raw = {"blocks": []}

    text_blocks: list[dict[str, Any]] = []
    figure_count = 0
    for block in raw.get("blocks", []):
        if block.get("type") == 1:
            figure_count += 1
            continue
        spans = _block_spans(block)
        content = _clean("\n".join(span.get("text", "") for span in spans))
        if not content:
            continue
        sizes = [float(span.get("size", 0.0)) for span in spans if span.get("text", "").strip()]
        flags = [int(span.get("flags", 0)) for span in spans]
        text_blocks.append(
            {
                "text": content,
                "bbox": [round(float(v), 2) for v in block.get("bbox", (0, 0, 0, 0))],
                "size": max(sizes) if sizes else 0.0,
                # PyMuPDF flag bit 4 (value 16) marks bold spans.
                "bold": any(flag & 16 for flag in flags),
            }
        )

    body_size = 0.0
    if text_blocks:
        sizes = [b["size"] for b in text_blocks if b["size"] > 0]
        if sizes:
            body_size = statistics.median(sizes)

    elements: list[PageElement] = []
    for index, block in enumerate(text_blocks):
        content: str = block["text"]
        lines = content.splitlines()
        size: float = block["size"]
        is_heading = (
            len(content) <= 160
            and len(lines) <= 2
            and (size >= body_size * 1.18 or (block["bold"] and size >= body_size))
        )
        if _looks_like_table(lines):
            kind = "table"
        elif _looks_like_equation(content):
            kind = "equation"
        elif is_heading:
            kind = "heading"
        else:
            kind = "text"
        elements.append(
            PageElement(
                index=index,
                kind=kind,
                text=content,
                bbox=block["bbox"],
                font_size=size,
                bold=bool(block["bold"]),
            )
        )

    # The heading is the largest heading-like element nearest the page top.
    heading = ""
    heading_candidates = [e for e in elements if e.kind == "heading"]
    if heading_candidates:
        heading_candidates.sort(key=lambda e: (-e.font_size, e.bbox[1]))
        heading = heading_candidates[0].text.replace("\n", " ").strip()
    elif elements:
        first = elements[0]
        if len(first.text) <= 120:
            heading = first.text.replace("\n", " ").strip()

    page_text = "\n\n".join(element.text for element in elements)

    preview_filename = ""
    if preview_dir is not None:
        preview_filename = _render_preview(page, page_number, preview_dir, preview_dpi, warnings)

    warning = ""
    if not page_text.strip():
        warning = "No text on this page (image-only or scanned)."

    return ExtractedPage(
        page_number=page_number,
        width=round(float(rect.width), 2),
        height=round(float(rect.height), 2),
        text=page_text,
        heading=heading,
        elements=elements,
        has_equations=any(e.kind == "equation" for e in elements),
        has_figures=figure_count > 0,
        has_tables=any(e.kind == "table" for e in elements),
        preview_filename=preview_filename,
        warning=warning,
    )


def _render_preview(
    page: pymupdf.Page,
    page_number: int,
    preview_dir: Path,
    dpi: int,
    warnings: list[str],
) -> str:
    try:
        preview_dir.mkdir(parents=True, exist_ok=True)
        pixmap = page.get_pixmap(dpi=dpi, alpha=False)
        filename = f"page-{page_number:04d}.webp"
        target = preview_dir / filename
        try:
            target.write_bytes(pixmap.pil_tobytes(format="WEBP", quality=82))
        except Exception:
            # Pillow is optional; PNG works from PyMuPDF alone.
            filename = f"page-{page_number:04d}.png"
            target = preview_dir / filename
            target.write_bytes(pixmap.tobytes("png"))
        return filename
    except Exception as exc:  # pragma: no cover - rendering edge cases
        warnings.append(f"Page {page_number}: preview could not be rendered ({exc}).")
        return ""
