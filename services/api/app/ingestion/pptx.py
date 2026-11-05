"""PowerPoint import: a .pptx becomes a PDF, and the PDF pipeline does the rest.

Rendering a slide deck faithfully needs PowerPoint or LibreOffice, neither of
which an installed Mac app can assume. What a student needs from a deck is
different: the words on each slide, the pictures, the tables, each at the
place it had on the slide, so citations can highlight it and the reader can
show it. python-pptx gives every shape's text and its position in EMUs;
PyMuPDF can draw text and images at those positions on a page of the slide's
own size. The result is a plain-looking but faithful PDF — same slide count,
same layout, same words — which :func:`app.ingestion.pdf.extract_pdf` then
treats exactly like an exported deck: headings, bounding boxes, previews,
topics and highlights all come for free.

What is lost: theme backgrounds, fonts, SmartArt and charts (rendered as their
text, when they have any), animations. Speaker notes are rendered in a grey
strip under the slide so the lecturer's own explanation is searchable; the
strip is outside the slide's area so highlights on slide content are unmoved.

Why not Docling: it reads PPTX too, but pulls PyTorch, which an 8 GB M1 with
an on-device embedding model cannot afford. python-pptx is pure Python.
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

log = logging.getLogger(__name__)

PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

#: EMUs per PDF point (914,400 per inch / 72).
_EMU_PER_POINT = 12_700.0
_ZIP_MAGIC = b"PK\x03\x04"
_NOTES_STRIP_POINTS = 90.0
_MIN_FONT = 7.0


def _font_names() -> tuple[str, str]:
    """(regular, bold) font names for drawing.

    PyMuPDF's built-in Base-14 fonts are WinAnsi only: a bullet, an em dash or
    a Greek letter comes out as "?", and Greek letters are exactly what lecture
    slides contain. `pymupdf-fonts` ships FiraGO, which covers Latin, Greek
    and Cyrillic and the common maths symbols; it is a declared dependency,
    and the Base-14 fallback is only for an environment that lacks it.
    """
    descriptors = getattr(pymupdf, "fitz_fontdescriptors", {})
    if "figo" in descriptors:
        return "figo", "figbo" if "figbo" in descriptors else "figo"
    return "helv", "hebo"


def looks_like_pptx(data: bytes, filename: str = "") -> bool:
    """True for an OOXML package that contains a presentation part.

    The suffix alone is not trusted: a renamed file would be sent to the wrong
    extractor and fail there with a confusing message.
    """
    if data[:4] != _ZIP_MAGIC:
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            names = set(package.namelist())
    except (zipfile.BadZipFile, OSError):
        return False
    return "ppt/presentation.xml" in names or (
        Path(filename).suffix.lower() == ".pptx" and "[Content_Types].xml" in names
    )


@dataclass
class RenderedDeck:
    pdf: bytes
    slide_count: int
    title: str = ""
    #: Non-fatal problems: pictures that could not be decoded, empty slides.
    warnings: list[str] = field(default_factory=list)


class UnreadablePresentationError(ValueError):
    """The file could not be opened as a presentation at all."""


def render_pptx_to_pdf(path: Path, *, max_slides: int) -> RenderedDeck:
    try:
        from pptx import Presentation
        from pptx.util import Emu
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise UnreadablePresentationError("python-pptx is not installed.") from exc

    try:
        deck = Presentation(str(path))
    except Exception as exc:
        raise UnreadablePresentationError(f"Not a readable PowerPoint file: {exc}") from exc

    width = _points(deck.slide_width or Emu(9_144_000))
    height = _points(deck.slide_height or Emu(6_858_000))
    slides = list(deck.slides)
    if not slides:
        raise UnreadablePresentationError("This presentation has no slides.")

    warnings: list[str] = []
    limit = min(len(slides), max_slides)
    if len(slides) > limit:
        warnings.append(f"Only the first {limit} of {len(slides)} slides were processed.")

    pdf = pymupdf.open()
    title = ""
    for number, slide in enumerate(slides[:limit], start=1):
        notes = _notes_text(slide)
        page = pdf.new_page(width=width, height=height + (_NOTES_STRIP_POINTS if notes else 0))
        drew_anything = False
        for shape in _iter_shapes(slide.shapes):
            try:
                drew_anything |= _draw_shape(page, shape, warnings, number)
            except Exception as exc:  # one odd shape must not lose the slide
                log.warning("pptx_shape_failed", extra={"slide": number, "detail": str(exc)})
                warnings.append(f"Slide {number}: one element could not be rendered ({exc}).")
        if not drew_anything:
            warnings.append(f"Slide {number} has no text or pictures that could be read.")
        if notes:
            _draw_notes(page, notes, top=height, width=width)
        if not title:
            title = _slide_title(slide)

    pdf.set_metadata({"title": title or path.stem, "producer": "StudyPilot python-pptx+pymupdf"})
    data = pdf.tobytes(garbage=3, deflate=True)
    pdf.close()
    return RenderedDeck(pdf=data, slide_count=limit, title=title, warnings=warnings)


# --- drawing ---------------------------------------------------------------
def _points(emu: int | float) -> float:
    return float(emu) / _EMU_PER_POINT


def _rect(shape) -> pymupdf.Rect:
    left = _points(shape.left or 0)
    top = _points(shape.top or 0)
    return pymupdf.Rect(
        left,
        top,
        left + max(_points(shape.width or 0), 1),
        top + max(_points(shape.height or 0), 1),
    )


def _iter_shapes(shapes):
    """Flatten group shapes; python-pptx reports children with absolute offsets."""
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_shapes(shape.shapes)
        else:
            yield shape


def _draw_shape(page: pymupdf.Page, shape, warnings: list[str], slide_number: int) -> bool:
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    rect = _rect(shape)
    if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
        try:
            page.insert_image(rect, stream=shape.image.blob, keep_proportion=True)
        except Exception as exc:
            warnings.append(
                f"Slide {slide_number}: a picture could not be decoded "
                f"({getattr(shape.image, 'ext', 'unknown')} format: {exc})."
            )
            page.draw_rect(rect, color=(0.6, 0.6, 0.6), width=0.5)
        return True

    if getattr(shape, "has_table", False) and shape.has_table:
        return _draw_table(page, shape, rect)

    if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
        text = _frame_text(shape)
        if not text.strip():
            return False
        size = _font_size(shape)
        _insert_shrinking(page, rect, text, size, bold=_is_title(shape))
        return True

    # Charts, SmartArt and the like: render whatever text they carry.
    if getattr(shape, "has_chart", False) and shape.has_chart:
        chart = shape.chart
        parts = []
        if chart.has_title:
            parts.append(chart.chart_title.text_frame.text)
        for plot in chart.plots:
            parts.extend(str(c) for c in plot.categories)
            parts.extend(s.name for s in plot.series if s.name)
        text = "\n".join(p for p in parts if p)
        if text:
            page.draw_rect(rect, color=(0.6, 0.6, 0.6), width=0.5)
            _insert_shrinking(page, rect, f"[Chart] {text}", 11)
            return True
    return False


def _frame_text(shape) -> str:
    lines: list[str] = []
    for paragraph in shape.text_frame.paragraphs:
        text = "".join(run.text for run in paragraph.runs) or paragraph.text
        text = text.replace("\x0b", " ").strip()
        if not text:
            continue
        level = paragraph.level or 0
        bullet = "• " if _is_body(shape) and len(shape.text_frame.paragraphs) > 1 else ""
        lines.append(("    " * level) + bullet + text)
    return "\n".join(lines)


def _is_body(shape) -> bool:
    if not getattr(shape, "is_placeholder", False):
        return True
    from pptx.enum.shapes import PP_PLACEHOLDER

    kind = shape.placeholder_format.type
    return kind not in {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE, PP_PLACEHOLDER.SUBTITLE}


def _is_title(shape) -> bool:
    if not getattr(shape, "is_placeholder", False):
        return False
    from pptx.enum.shapes import PP_PLACEHOLDER

    return shape.placeholder_format.type in {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE}


def _font_size(shape) -> float:
    """The first explicit run size, else a size by role.

    Titles are drawn larger so :mod:`app.ingestion.pdf`'s heading detection,
    which keys on relative font size, finds them.
    """
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            if run.font.size:
                return max(_MIN_FONT, min(run.font.size.pt, 60))
    return 28.0 if _is_title(shape) else 16.0


def _insert_shrinking(
    page: pymupdf.Page, rect: pymupdf.Rect, text: str, size: float, *, bold: bool = False
) -> None:
    """Fit the text in its box by shrinking the font, never by dropping words."""
    regular, heavy = _font_names()
    fontname = heavy if bold else regular
    fontsize = size
    while fontsize >= _MIN_FONT:
        remaining = page.insert_textbox(rect, text, fontsize=fontsize, fontname=fontname, align=0)
        if remaining >= 0:
            return
        fontsize -= 1
    # Still too long at the minimum: let it overflow the box rather than vanish.
    tall = pymupdf.Rect(rect.x0, rect.y0, rect.x1, page.rect.y1)
    page.insert_textbox(tall, text, fontsize=_MIN_FONT, fontname=fontname, align=0)


def _inset(rect: pymupdf.Rect, margin: float) -> pymupdf.Rect:
    return pymupdf.Rect(rect.x0 + margin, rect.y0 + margin, rect.x1 - margin, rect.y1 - margin)


def _draw_table(page: pymupdf.Page, shape, rect: pymupdf.Rect) -> bool:
    table = shape.table
    col_widths = [_points(c.width) for c in table.columns]
    row_heights = [_points(r.height) for r in table.rows]
    y = rect.y0
    drew = False
    for r_index, row in enumerate(table.rows):
        x = rect.x0
        row_h = row_heights[r_index] or 18.0
        for c_index, cell in enumerate(row.cells):
            col_w = col_widths[c_index] or 60.0
            cell_rect = pymupdf.Rect(x, y, x + col_w, y + row_h)
            page.draw_rect(cell_rect, color=(0.5, 0.5, 0.5), width=0.5)
            text = cell.text.strip()
            if text:
                _insert_shrinking(page, _inset(cell_rect, 2), text, 11)
                drew = True
            x += col_w
        y += row_h
    return drew


def _notes_text(slide) -> str:
    if not slide.has_notes_slide:
        return ""
    frame = slide.notes_slide.notes_text_frame
    return frame.text.strip() if frame is not None else ""


def _draw_notes(page: pymupdf.Page, notes: str, *, top: float, width: float) -> None:
    strip = pymupdf.Rect(0, top, width, top + _NOTES_STRIP_POINTS)
    page.draw_rect(strip, color=None, fill=(0.95, 0.95, 0.95))
    _insert_shrinking(page, _inset(strip, 10), f"Speaker notes: {notes}", 9)


def _slide_title(slide) -> str:
    try:
        shape = slide.shapes.title
    except Exception:
        shape = None
    if shape is not None and shape.has_text_frame and shape.text_frame.text.strip():
        return shape.text_frame.text.strip()[:200]
    return ""
