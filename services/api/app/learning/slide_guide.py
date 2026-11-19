"""Teaching slides written for one page of a lecture.

A lecturer's slide is a prompt for someone speaking, not an explanation. This
turns one slide into one or more teaching slides a student can learn from
alone: what it says, every equation unpacked, a diagram or a plotted curve
where a picture is clearer, a worked example, and phrases for finding a
lecture video on it.

Three things are decided here rather than left to the model.

**What is the slide and what is not.**  The slide is the subject, not the
limit — fully understanding a slide usually needs background it does not
state.  So the model may add it, but only in slides of kind ``background`` or
``example``, which the reader labels as going beyond the slide.

**No model-written links.**  A model asked for a video URL invents one, and an
invented link is a dead link.  It is asked for search phrases instead, and the
links are built here, so every one of them leads somewhere.

**A format LaTeX survives.**  The reply is delimited Markdown, not JSON: a
backslash in ``\\frac`` inside a JSON string is exactly what models get wrong.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote_plus

from app.agents.prompts import _INJECTION_GUARD, _LEVEL_GUIDANCE
from app.models.base import (
    Attachment,
    ConsentRequiredError,
    GenerationRequest,
    ProviderUnavailableError,
)

log = logging.getLogger(__name__)

KINDS = (
    "terms",
    "overview",
    "explanation",
    "equation",
    "visual",
    "example",
    "background",
    "in_class",
    "check",
)
#: Kinds whose content is the model's own, not the slide's.
BEYOND_THE_SLIDE = frozenset({"example", "background"})
#: The teaching slides themselves, and the terms table in front of them.
MAX_CARDS = 7
MAX_VIDEO_QUERIES = 3
#: Longest side of the slide image sent to a model, in pixels.
IMAGE_MAX_SIDE = 1400

SYSTEM = (
    "You are StudyPilot. You turn one lecture slide into teaching slides a student can "
    "learn from on their own, without the lecturer. You teach like the best lecturer the "
    "student ever had: you build intuition first, then the formal statement, then let the "
    "student see it and try it.\n\n"
    f"{_INJECTION_GUARD}\n\n"
    "Before anything is explained, say what its words and symbols mean:\n"
    "- If the slide uses any technical term, abbreviation or symbol, the FIRST teaching "
    "slide is of kind `terms`, titled `Terms and symbols`, and holds nothing but tables. "
    "A student reads it before the explanation, so nothing later may use a term or symbol "
    "that it has not defined.\n"
    "- Technical terms and abbreviations go in a table with exactly these columns: "
    "`| Term | What it means |`. One row per term, in the order the slide uses them. "
    "Define each in one or two plain sentences, as you would to someone meeting the "
    "subject for the first time, and say what it is in this slide's context, not only in "
    "general: what the thing is, and what it connects, measures or is measured between. "
    "An abbreviation is written out in full first.\n"
    "- Symbols go in a second table with exactly these columns: "
    "`| Symbol | Name | What it means |`. The symbol in LaTeX, then its name in words as "
    "it is said aloud (`theta sub i`, `x bar`, `sigma squared`), then what "
    "quantity it stands for here, with its unit if it has one. One row per symbol, "
    "including every subscript or superscript form that means something different, and "
    "every symbol in the slide's figures and equations.\n"
    "- Leave out a table that would be empty, and leave out the `terms` slide altogether "
    "only when the slide has no technical term and no symbol at all, as on a title or "
    "agenda slide.\n"
    "- Inside a table cell never write the character `|`, not even in mathematics: write "
    "`\\lvert x \\rvert` and `\\mid` instead.\n\n"
    "What to write after that:\n"
    "- Explain everything on the slide so that a student who missed the lecture fully "
    "understands it: every term, every bullet, every figure and every equation.\n"
    "- The slide is the subject, not the limit. You may use well-established background "
    "knowledge to make it understandable, but anything that is not on the slide goes in a "
    "teaching slide of kind `background` or `example`, never mixed into the others.\n"
    "- After the `terms` slide, write 1 to 5 teaching slides. One is enough for a title "
    "or agenda slide; a dense slide deserves more. Never pad, and never repeat the "
    "slide's bullets back unexplained.\n"
    "- Mathematics in LaTeX: $...$ inline, $$...$$ on its own line. For each equation say "
    "what the equation states in words and when it is used; its symbols are already in "
    "the table, so refer to them by the names given there.\n"
    "- Never write a link or a URL.\n"
    "- When values are compared across the same few attributes, use a small Markdown "
    "table of at most 5 columns rather than a list.\n\n"
    "When a class transcript is supplied:\n"
    "- It is what the lecturer said while this slide was up, transcribed by machine, so "
    "expect misheard words and fix them silently. It may run into the neighbouring "
    "slides; use only what is about this slide.\n"
    "- Teach the slide the way the lecturer did: their order, their emphasis, their "
    "examples and analogies, and anything they said about what matters for the exam.\n"
    "- Put what the lecturer said that the slide does not show in one teaching slide of "
    "kind `in_class`, in your own clear words. Never invent something the lecturer said, "
    "and never quote more than a short phrase.\n"
    "- Then add your own knowledge where the lecturer was brief, under `background` or "
    "`example` as usual.\n\n"
    "Show it, do not only tell it. Use the visual that fits; a concept slide should have "
    "at least one, a title slide needs none:\n"
    "- A structure, process or dependency: a fenced ```mermaid block. Use `flowchart LR` "
    "or `flowchart TD` for pipelines, `sequenceDiagram` for exchanges over time, "
    "`stateDiagram-v2` for states, `mindmap` for how ideas group, `timeline` for history. "
    "Short plain-text labels, no HTML, no styling.\n"
    "- Fixed numbers or a measured curve: a fenced ```plot block holding one JSON object: "
    '{"title": "...", "x": "x-axis label", "y": "y-axis label", '
    '"series": [{"name": "...", "points": [[x, y], ...]}]} '
    "with at most 3 series of 12 to 40 points each. Compute the points correctly.\n"
    "- A formula whose behaviour depends on a parameter (a frequency, a window length, a "
    "learning rate, a filter order): a fenced ```explore block, which the student drives "
    "with sliders. One JSON object: "
    '{"title": "...", "x": {"label": "t (ms)", "min": 0, "max": 20}, '
    '"y": {"label": "amplitude"}, '
    '"params": [{"name": "f", "label": "frequency (Hz)", "min": 50, "max": 500, '
    '"value": 100, "step": 10}], '
    '"curves": [{"name": "signal", "expr": "sin(2*pi*f*x/1000)"}], '
    '"note": "one sentence saying what to try and what to notice"}. '
    "At most 3 params and 3 curves. An expr uses x, the param names, numbers, + - * / ^, "
    "parentheses and these functions only: sin cos tan asin acos atan sinh cosh tanh exp "
    "ln log10 log2 sqrt abs floor ceil round sign min max sinc rect step, and the "
    "constants pi and e. Always write multiplication with *. Prefer this to a plot "
    "whenever a parameter is what the slide is about.\n"
    "- A derivation, an algorithm or a calculation with an order to it: a fenced ```steps "
    "block. Each step starts with a line `## <step title>` followed by its Markdown "
    "(LaTeX allowed, no code fences inside). 3 to 7 steps. The student reveals them one "
    "at a time.\n"
    "- One sentence the student must not miss: a quote line starting with "
    "`> **Key idea:**`, `> **Common mistake:**` or `> **Exam tip:**`. At most two per "
    "teaching slide.\n"
    "- Finish a slide that teaches a concept with one teaching slide of kind `check`: a "
    "fenced ```quiz block of 2 to 4 pairs of lines `Q: <question>` and `A: <answer>`, "
    "testing understanding rather than recall of wording. The answers stay hidden until "
    "the student asks for them.\n\n"
    "Reply in exactly this format, with nothing before or after it:\n\n"
    "=== SLIDE | <kind> | <short title> ===\n"
    "<Markdown body>\n\n"
    "(one block per teaching slide)\n\n"
    "=== VIDEOS ===\n"
    "- <a search phrase that would find a good lecture video on this slide's topic>\n"
    "(1 to 3 phrases, most specific first)\n\n"
    f"<kind> is one of: {', '.join(KINDS)}."
)

_HEADER = re.compile(r"^===\s*SLIDE\s*\|\s*([a-zA-Z_ ]+?)\s*\|\s*(.+?)\s*===\s*$", re.MULTILINE)
_VIDEOS = re.compile(r"^===\s*VIDEOS\s*===\s*$", re.MULTILINE)
_MD_LINK = re.compile(r"\[([^\]\n]+)\]\((?:https?://|www\.)[^)\s]*\)")
_BARE_URL = re.compile(r"(?:https?://|www\.)\S+")


@dataclass
class GuideCard:
    kind: str
    title: str
    markdown: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "title": self.title, "markdown": self.markdown}


@dataclass
class GuideDraft:
    cards: list[GuideCard]
    video_queries: list[str]
    #: "llm:<provider>" when a model wrote it, "source_outline" when it is the slide's own text.
    generated_by: str
    model: str = ""
    saw_image: bool = False
    used_transcript: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def written(self) -> bool:
        return self.generated_by.startswith("llm:")


def video_links(queries: list[str]) -> list[dict[str, str]]:
    """Search links, built here so that none of them can be an invented address."""
    return [
        {
            "title": query,
            "url": f"https://www.youtube.com/results?search_query={quote_plus(query)}",
        }
        for query in queries
    ]


def _without_links(markdown: str) -> str:
    """Keep a link's words, drop its address: the model was told not to write one."""
    return _BARE_URL.sub("", _MD_LINK.sub(r"\1", markdown))


def parse_guide(text: str) -> tuple[list[GuideCard], list[str]]:
    """Split a model reply into teaching slides and video search phrases.

    Tolerant on purpose: a reply with no header at all is still one usable
    slide, and an unknown kind is an explanation.
    """
    body = text.strip()
    queries: list[str] = []
    videos = _VIDEOS.search(body)
    if videos:
        for line in body[videos.end() :].splitlines():
            phrase = _without_links(line.strip().lstrip("-*•0123456789. ").strip()).strip()
            if phrase:
                queries.append(phrase[:120])
        body = body[: videos.start()]

    headers = list(_HEADER.finditer(body))
    cards: list[GuideCard] = []
    for index, match in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(body)
        markdown = _without_links(body[match.end() : end]).strip()
        if not markdown:
            continue
        kind = match.group(1).strip().lower().replace(" ", "_")
        cards.append(
            GuideCard(
                kind=kind if kind in KINDS else "explanation",
                title=match.group(2).strip()[:160],
                markdown=markdown,
            )
        )
    if not cards and body.strip():
        cards.append(GuideCard("explanation", "Explanation", _without_links(body).strip()))
    # The definitions are read first, wherever the model happened to put them.
    cards.sort(key=lambda card: card.kind != "terms")
    return cards[:MAX_CARDS], queries[:MAX_VIDEO_QUERIES]


def render_slide_image(pdf_bytes: bytes, page_number: int) -> bytes | None:
    """The page as a PNG a vision model can read, or None if it cannot be drawn."""
    try:
        import pymupdf

        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            if not 1 <= page_number <= doc.page_count:
                return None
            page = doc[page_number - 1]
            longest = max(page.rect.width, page.rect.height) or 1.0
            scale = min(2.0, IMAGE_MAX_SIDE / longest)
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            return pixmap.tobytes("png")
    except Exception as exc:  # a slide that cannot be drawn is explained from its text
        log.warning("slide_image_failed", extra={"reason": str(exc)})
        return None


def build_prompt(
    *,
    course_title: str,
    document_title: str,
    page_number: int,
    page_count: int,
    heading: str,
    text: str,
    previous_heading: str,
    next_heading: str,
    level: str,
    has_image: bool,
    transcript_excerpts: list[str] | None = None,
) -> str:
    lines = [
        f"Course: {course_title or 'n/a'}",
        f"Lecture: {document_title or 'n/a'}",
        f"Slide {page_number} of {page_count}" + (f" — {heading}" if heading else ""),
    ]
    if previous_heading:
        lines.append(f"The slide before it: {previous_heading}")
    if next_heading:
        lines.append(f"The slide after it: {next_heading}")
    lines.append(_LEVEL_GUIDANCE.get(level, _LEVEL_GUIDANCE["intermediate"]))
    if has_image:
        lines.append(
            "The slide itself is attached as an image: read its figures, diagrams and "
            "equations from the image, and use the extracted text below for its wording."
        )
    slide_text = text.strip() or "(no text could be extracted from this slide)"
    spoken = ""
    if transcript_excerpts:
        joined = "\n\n[...]\n\n".join(excerpt.strip() for excerpt in transcript_excerpts)
        spoken = (
            "\n\nClass transcript, the parts most likely about this slide, in the order "
            f"they were said:\n<<<\n{joined[:6000]}\n>>>"
        )
    return (
        "\n".join(lines)
        + f"\n\nText extracted from the slide:\n<<<\n{slide_text[:6000]}\n>>>"
        + spoken
        + "\n\nWrite the teaching slides for this slide."
    )


def outline_from_text(heading: str, text: str) -> GuideDraft:
    """What can be said without a model: the slide's own words, set out to be read."""
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [line for line in lines if line and line != heading]
    if lines:
        markdown = "\n".join(f"- {line}" for line in lines[:40])
    else:
        markdown = (
            "No text could be extracted from this slide, so there is nothing to set out "
            "here. It is probably a picture; a model that reads images can explain it."
        )
    return GuideDraft(
        cards=[GuideCard("overview", heading or "What this slide says", markdown)],
        video_queries=[heading] if heading else [],
        generated_by="source_outline",
        warnings=[
            "This is the slide's own text, not an explanation. Choose an AI model in "
            "Settings and allow it to read your material to have each slide explained."
        ],
    )


def write_guide(
    *,
    provider: Any,
    course_title: str,
    document_title: str,
    page_number: int,
    page_count: int,
    heading: str,
    text: str,
    previous_heading: str = "",
    next_heading: str = "",
    level: str = "intermediate",
    slide_image: bytes | None = None,
    transcript_excerpts: list[str] | None = None,
) -> GuideDraft:
    """Ask the model for the teaching slides; fall back to the slide's text if it cannot."""
    info = provider.info
    if not info.generative:
        return outline_from_text(heading, text)

    image = slide_image if (slide_image and info.accepts_images) else None
    request = GenerationRequest(
        system=SYSTEM,
        user=build_prompt(
            course_title=course_title,
            document_title=document_title,
            page_number=page_number,
            page_count=page_count,
            heading=heading,
            text=text,
            previous_heading=previous_heading,
            next_heading=next_heading,
            level=level,
            has_image=image is not None,
            transcript_excerpts=transcript_excerpts,
        ),
        question=heading,
        max_output_tokens=5200,
        timeout_seconds=150.0,
        attachments=(
            [Attachment(media_type="image/png", data=image, label=f"slide {page_number}")]
            if image
            else []
        ),
    )
    try:
        result = provider.generate(request)
    except (ProviderUnavailableError, ConsentRequiredError) as exc:
        log.warning("slide_guide_failed", extra={"reason": str(exc)})
        draft = outline_from_text(heading, text)
        draft.warnings.insert(0, f"The AI model could not be used: {exc}")
        return draft

    cards, queries = parse_guide(result.text)
    if not cards:
        draft = outline_from_text(heading, text)
        draft.warnings.insert(0, "The model returned nothing usable for this slide.")
        return draft
    return GuideDraft(
        cards=cards,
        video_queries=queries or ([heading] if heading else []),
        generated_by=f"llm:{result.provider}",
        model=result.model,
        saw_image=image is not None,
        used_transcript=bool(transcript_excerpts),
        warnings=list(result.warnings),
    )
