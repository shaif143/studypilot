"""Class transcripts: reading one, and finding what was said about a slide.

A slide is what the lecturer showed; the transcript is what they said while
it was up. The explanation of a slide is better for both, so the part of the
transcript that belongs to a slide is put in front of the model that explains
it.

The transcript of a lecture is far longer than a prompt should be, and it
carries no slide markers. The part for a slide is found two ways at once:
by its words (a passage that uses the slide's own terms is about the slide)
and by its place (slide 10 of 40 is spoken about a quarter of the way in).
Neither is reliable alone — a term recurs all lecture, and a lecturer lingers
on one slide for ten minutes — so the score is their sum.
"""

from __future__ import annotations

import io
import math
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from xml.etree import ElementTree

#: Longest transcript kept, in characters: a three-hour class is about 180 000.
MAX_TRANSCRIPT_CHARS = 600_000
#: What a transcript file may be. Subtitle formats lose their timestamps.
TRANSCRIPT_SUFFIXES = frozenset({".txt", ".md", ".vtt", ".srt", ".docx", ".pdf"})

_TIMESTAMP_LINE = re.compile(
    r"^\s*\d{1,2}:\d{2}(?::\d{2})?[.,]?\d*\s*-->\s*\d{1,2}:\d{2}(?::\d{2})?[.,]?\d*.*$"
)
_CUE_NUMBER = re.compile(r"^\s*\d+\s*$")
_INLINE_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_LEADING_STAMP = re.compile(r"^\s*\[?\(?\d{1,2}:\d{2}(?::\d{2})?\)?\]?\s*[-–:]?\s*")
_WORD = re.compile(r"[a-z][a-z0-9\-']{2,}")
_STOP = frozenset(
    [
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "from",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "not",
        "but",
        "you",
        "your",
        "our",
        "can",
        "will",
        "would",
        "could",
        "should",
        "about",
        "into",
        "over",
        "under",
        "than",
        "then",
        "them",
        "they",
        "their",
        "there",
        "here",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "how",
        "also",
        "just",
        "like",
        "more",
        "most",
        "some",
        "such",
        "very",
        "much",
        "many",
        "any",
        "all",
        "each",
        "these",
        "those",
        "its",
        "it's",
        "one",
        "two",
        "okay",
        "right",
        "yeah",
        "gonna",
        "going",
        "know",
        "think",
        "mean",
        "actually",
        "basically",
        "so",
        "because",
        "been",
        "being",
        "does",
        "did",
        "doing",
        "get",
        "got",
        "let",
        "lets",
        "make",
        "made",
        "see",
        "look",
        "say",
        "said",
        "thing",
        "things",
        "kind",
        "sort",
        "bit",
        "little",
        "slide",
        "slides",
        "lecture",
    ]
)


class TranscriptError(ValueError):
    """A file that cannot be used as a transcript; the message is for the user."""


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise TranscriptError("That Word file could not be read.") from exc
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise TranscriptError("That Word file could not be read.") from exc
    paragraphs = []
    for paragraph in root.iter(f"{namespace}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t"))
        if text.strip():
            paragraphs.append(text.strip())
    return "\n".join(paragraphs)


def _pdf_text(data: bytes) -> str:
    try:
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as doc:
            flags = pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES
            return "\n".join(page.get_text("text", flags=flags) for page in doc)
    except Exception as exc:
        raise TranscriptError("That PDF could not be read.") from exc


def clean_transcript(text: str) -> str:
    """Spoken words only: no cue numbers, timestamps or markup, one paragraph per line run."""
    lines: list[str] = []
    last_said = ""
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if line.upper().startswith(("WEBVTT", "NOTE ", "KIND:", "LANGUAGE:")):
            continue
        if _TIMESTAMP_LINE.match(line) or _CUE_NUMBER.match(line):
            continue
        line = _LEADING_STAMP.sub("", _INLINE_TAG.sub("", line)).strip()
        if not line:
            continue
        # Captions repeat a line as it scrolls; keep one. The repeat is the
        # last thing said, whatever blank lines the cues put between them.
        if line == last_said:
            continue
        last_said = line
        lines.append(line)
    return "\n".join(lines).strip()


def read_transcript(filename: str, data: bytes) -> str:
    """The words of an uploaded transcript file, or a refusal that says why."""
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in TRANSCRIPT_SUFFIXES:
        raise TranscriptError("A transcript is a text file: .txt, .md, .vtt, .srt, .docx or .pdf.")
    if suffix == ".docx":
        text = _docx_text(data)
    elif suffix == ".pdf":
        text = _pdf_text(data)
    else:
        text = ""
        for encoding in ("utf-8-sig", "utf-16", "latin-1"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
    text = clean_transcript(text)
    if len(text.split()) < 20:
        raise TranscriptError("That file has almost no words in it, so it is not a transcript.")
    return text[:MAX_TRANSCRIPT_CHARS]


# --- finding the part for a slide ------------------------------------------------------
def _terms(text: str) -> list[str]:
    return [word for word in _WORD.findall(text.lower()) if word not in _STOP]


@dataclass
class Passage:
    text: str
    #: Where its middle falls in the transcript, 0 to 1.
    position: float


def split_passages(transcript: str, *, words_per_passage: int = 170) -> list[Passage]:
    """The transcript in overlapping passages of about a minute of speech each."""
    words = transcript.split()
    if not words:
        return []
    step = max(1, words_per_passage * 2 // 3)
    passages: list[Passage] = []
    for start in range(0, len(words), step):
        chunk = words[start : start + words_per_passage]
        if len(chunk) < 25 and passages:
            break
        middle = (start + len(chunk) / 2) / len(words)
        passages.append(Passage(text=" ".join(chunk), position=middle))
    return passages


def excerpts_for_slide(
    transcript: str,
    *,
    slide_text: str,
    heading: str,
    page_number: int,
    page_count: int,
    max_chars: int = 4200,
) -> list[str]:
    """What the lecturer most likely said about this slide, in the order it was said."""
    passages = split_passages(transcript)
    if not passages:
        return []
    if len(transcript) <= max_chars:
        return [transcript.strip()]

    slide_terms = Counter(_terms(f"{heading} {heading} {slide_text}"))
    # A term in every passage tells nothing; weight by how few passages hold it.
    holding: Counter[str] = Counter()
    passage_terms: list[Counter[str]] = []
    for passage in passages:
        counts = Counter(_terms(passage.text))
        passage_terms.append(counts)
        holding.update(counts.keys())
    total = len(passages)
    expected = (page_number - 0.5) / max(1, page_count)

    scored: list[tuple[float, int]] = []
    for index, (passage, counts) in enumerate(zip(passages, passage_terms, strict=True)):
        lexical = 0.0
        for term, weight in slide_terms.items():
            if term in counts:
                rarity = math.log(1 + total / holding[term])
                lexical += min(weight, 3) * rarity * (1 + math.log(counts[term]))
        norm = math.sqrt(sum(counts.values())) or 1.0
        lexical /= norm
        # A wide bell around where the slide falls in the lecture.
        distance = abs(passage.position - expected)
        place = math.exp(-((distance / 0.18) ** 2))
        scored.append((lexical * (0.45 + place) + 0.35 * place, index))

    scored.sort(reverse=True)
    chosen: list[int] = []
    used = 0
    for _score, index in scored:
        length = len(passages[index].text)
        if used + length > max_chars:
            continue
        # Neighbours overlap by a third; taking both repeats the same sentences.
        if any(abs(index - other) == 1 for other in chosen) and len(chosen) >= 2:
            continue
        chosen.append(index)
        used += length
        if len(chosen) >= 4:
            break
    return [passages[index].text for index in sorted(chosen)]
