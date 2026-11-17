"""Jupyter notebooks and HTML pages, laid out as pages.

A tutorial is as much course material as a lecture, and it usually arrives
as a notebook (``.ipynb``) or as the HTML a notebook was exported to. Neither
has pages, and everything else here — the reader, citations with a place on
the page, the slide view — is built on pages. So both are turned into a PDF,
the way a PowerPoint deck is, and that PDF is what is read, indexed and shown.

Both go through the same three steps:

1. **To plain HTML.**  A notebook's cells become HTML: Markdown cells through
   a small converter, code cells as preformatted source followed by their
   saved outputs (text, error traces, images). An HTML file is taken as it is.
2. **Cleaned.**  Only structure survives: headings, paragraphs, lists, code,
   tables and images that are *inside the file*. Scripts, styles, forms and
   frames are dropped, and so is every image that would have to be fetched:
   opening a file must never make a request to somebody's server.
3. **Laid out.**  PyMuPDF's Story engine flows the clean HTML onto A4 pages.
   A top-level heading starts a new page, so a page is a section and can be
   explained as one.

Nothing is executed: a notebook's code is shown, never run, and an HTML
file's scripts are discarded before layout. Mathematics stays as the LaTeX
source the author wrote, which is readable and is what a model explaining
the page reads best.
"""

from __future__ import annotations

import base64
import binascii
import html
import io
import json
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pymupdf

from app.ingestion.pptx import RenderedDeck

log = logging.getLogger(__name__)

NOTEBOOK_MEDIA_TYPE = "application/x-ipynb+json"
HTML_MEDIA_TYPE = "text/html"
NOTEBOOK_SUFFIXES = frozenset({".ipynb"})
HTML_SUFFIXES = frozenset({".html", ".htm"})
#: What Google Drive calls a Colab notebook, and the other names a notebook goes by.
NOTEBOOK_MIMES = frozenset(
    {NOTEBOOK_MEDIA_TYPE, "application/vnd.google.colaboratory", "application/x-ipynb"}
)

#: Lines of one cell's output that are kept; a training loop prints thousands.
MAX_OUTPUT_LINES = 60
MAX_OUTPUT_CHARS = 6000
#: Characters on a line of code before it is folded onto the next.
CODE_WRAP = 92
MAX_IMAGES = 200
#: A top-level heading starts a new page only after this much has been said.
MIN_SECTION_CHARS = 280
MAX_IMAGE_BYTES = 12 * 1024 * 1024

_PAGE = pymupdf.paper_rect("a4")
_MARGIN = 46.0
_CONTENT_WIDTH = _PAGE.width - 2 * _MARGIN

_CSS = """
body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.4; color: #1a1a1a; }
h1 { font-size: 19pt; margin: 0 0 8pt 0; }
h2 { font-size: 15pt; margin: 12pt 0 6pt 0; }
h3 { font-size: 12.5pt; margin: 10pt 0 4pt 0; }
h4, h5, h6 { font-size: 11pt; margin: 8pt 0 4pt 0; }
h1.break, h2.break { page-break-before: always; }
p { margin: 0 0 6pt 0; }
ul, ol { margin: 0 0 6pt 0; padding-left: 16pt; }
li { margin: 0 0 2pt 0; }
pre { font-family: monospace; font-size: 8.4pt; line-height: 1.3; background-color: #f1f2f5;
      padding: 5pt; margin: 3pt 0 6pt 0; white-space: pre-wrap; }
pre.out { background-color: #ffffff; border-left: 2pt solid #c9ccd6; color: #333333; }
pre.err { background-color: #fdf0ef; border-left: 2pt solid #c8443a; color: #7a231c; }
code { font-family: monospace; font-size: 9.4pt; background-color: #f1f2f5; }
blockquote { margin: 4pt 0 6pt 10pt; color: #444444; }
table { border-collapse: collapse; margin: 4pt 0 8pt 0; font-size: 9.5pt; }
td, th { border: 0.5pt solid #b9bcc6; padding: 2pt 5pt; }
th { background-color: #f1f2f5; }
p.label { font-size: 7.5pt; color: #7a7f8c; margin: 6pt 0 1pt 0; }
p.note { font-size: 8.5pt; color: #7a7f8c; }
"""


class UnreadableNotebookError(ValueError):
    """The file could not be read as a notebook or an HTML page at all."""


# --- recognising the files ---------------------------------------------------------------
def looks_like_notebook(data: bytes, filename: str = "") -> bool:
    """A notebook is JSON with cells and a format number, whatever it is called."""
    head = data[:4096].lstrip()
    if not head.startswith(b"{"):
        return False
    if Path(filename).suffix.lower() in NOTEBOOK_SUFFIXES:
        return True
    return b'"cells"' in data[:200_000] and b'"nbformat"' in data


def looks_like_html(data: bytes, filename: str = "") -> bool:
    """An HTML page by its name and its opening, never by its name alone."""
    head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    opens_as_html = head.startswith((b"<!doctype html", b"<html")) or b"<html" in head
    if Path(filename).suffix.lower() in HTML_SUFFIXES:
        return opens_as_html or b"<body" in head or b"<head" in head or b"<p" in head
    return head.startswith((b"<!doctype html", b"<html"))


# --- images kept beside the HTML ---------------------------------------------------------
@dataclass
class Images:
    """Pictures taken out of the file, to be handed to the layout engine by name."""

    items: dict[str, bytes] = field(default_factory=dict)
    dropped_remote: int = 0
    dropped_bad: int = 0

    def add(self, data: bytes) -> tuple[str, float, float] | None:
        """Keep a picture; returns its name and the size to draw it at, in points."""
        if len(self.items) >= MAX_IMAGES or not data or len(data) > MAX_IMAGE_BYTES:
            self.dropped_bad += 1
            return None
        try:
            # Opened only to check it is a picture and to learn its size; the
            # bytes are passed on as they are, transparency included.
            pixmap = pymupdf.Pixmap(data)
            width, height = pixmap.width, pixmap.height
        except Exception:
            self.dropped_bad += 1
            return None
        if width < 2 or height < 2:
            self.dropped_bad += 1
            return None
        # Screen pixels to points, then shrunk to fit the column and most of a page.
        scale = min(0.75, _CONTENT_WIDTH / width, (_PAGE.height * 0.62) / height)
        name = f"image-{len(self.items) + 1}.png"
        self.items[name] = data
        return name, width * scale, height * scale

    def add_data_uri(self, uri: str) -> tuple[str, float, float] | None:
        match = re.match(r"data:image/[a-zA-Z0-9.+-]+;base64,(.*)", uri.strip(), re.DOTALL)
        if not match:
            self.dropped_bad += 1
            return None
        try:
            return self.add(base64.b64decode(match.group(1), validate=False))
        except (binascii.Error, ValueError):
            self.dropped_bad += 1
            return None


# --- cleaning HTML -----------------------------------------------------------------------
_DROP_WITH_CONTENT = frozenset(
    {"script", "style", "head", "noscript", "template", "iframe", "object", "embed", "svg",
     "math", "form", "button", "select", "textarea", "nav", "audio", "video", "canvas"}
)  # fmt: skip
_KEEP = frozenset(
    {"h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "pre", "code", "blockquote",
     "table", "thead", "tbody", "tr", "td", "th", "b", "strong", "i", "em", "br", "hr",
     "sub", "sup", "dl", "dt", "dd"}
)  # fmt: skip
_VOID = frozenset({"br", "hr", "img", "meta", "link", "input", "area", "base", "col", "wbr"})
#: Jupyter's export decorates every cell with these; they are furniture, not content.
_SKIP_CLASSES = ("prompt", "anchor-link", "jp-inputprompt", "jp-outputprompt", "collapser")


def _wrap_code(text: str) -> str:
    """Fold over-long lines of code, so none runs off the page."""
    folded: list[str] = []
    for original in text.split("\n"):
        rest = original
        while len(rest) > CODE_WRAP:
            folded.append(rest[:CODE_WRAP])
            rest = "    " + rest[CODE_WRAP:]
        folded.append(rest)
    return "\n".join(folded)


class _Cleaner(HTMLParser):
    """Rewrites HTML keeping structure, text and embedded pictures, and nothing else."""

    def __init__(self, images: Images) -> None:
        super().__init__(convert_charrefs=True)
        self.images = images
        self.out: list[str] = []
        self.title = ""
        self._skip: list[str] = []  # open elements whose content is dropped
        self._in_title = False
        self._pre = 0
        self._pre_text: list[str] = []
        self._since_break = 0  # characters of content since the last page break

    # A tag that is dropped keeps its text; a tag that is skipped loses it too.
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        if tag == "title" and not self.title:
            # Read even though it sits in <head>, which is otherwise dropped whole.
            self._in_title = True
            return
        if self._skip:
            if tag not in _VOID:
                self._skip.append(tag)
            return
        classes = attributes.get("class", "").lower()
        if tag in _DROP_WITH_CONTENT or any(name in classes for name in _SKIP_CLASSES):
            if tag not in _VOID:
                self._skip.append(tag)
            return
        if "hidden" in attributes and tag not in _VOID:
            self._skip.append(tag)
            return
        if tag == "img":
            self._image(attributes)
            return
        if self._pre:
            if tag == "br":
                self._pre_text.append("\n")
            return  # highlighting spans inside code are dropped, their text kept
        if tag == "pre":
            self._pre = 1
            self._pre_text = []
            kind = "err" if "error" in classes or "stderr" in classes else ""
            self.out.append(f'<pre class="{kind}">' if kind else "<pre>")
            return
        if tag in ("h1", "h2"):
            # A top-level heading opens a page, so that a page is a section,
            # unless almost nothing has been said since the last one: a title
            # followed at once by a subtitle belongs on one page.
            fresh_page = self._since_break >= MIN_SECTION_CHARS
            self.out.append(f'<{tag} class="break">' if fresh_page else f"<{tag}>")
            if fresh_page:
                self._since_break = 0
            return
        if tag in _KEEP:
            self.out.append(f"<{tag}>")
        elif tag in ("div", "section", "article", "main", "figure", "figcaption", "details"):
            self.out.append("<div>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self._in_title = False
            return
        if self._skip:
            # Close the innermost matching element; tolerate unclosed ones inside it.
            if tag in self._skip:
                while self._skip and self._skip.pop() != tag:
                    pass
            return
        if self._pre:
            if tag == "pre":
                self._pre = 0
                text = "".join(self._pre_text).strip("\n")
                self.out.append(html.escape(_wrap_code(text), quote=False) + "</pre>")
            return
        if tag in _KEEP and tag not in _VOID:
            self.out.append(f"</{tag}>")
        elif tag in ("div", "section", "article", "main", "figure", "figcaption", "details"):
            self.out.append("</div>")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        self._since_break += len(data.strip())
        if self._pre:
            self._pre_text.append(data)
            return
        if data.strip():
            self.out.append(html.escape(data, quote=False))
        elif data:
            self.out.append(" ")

    def _image(self, attributes: dict[str, str]) -> None:
        source = attributes.get("src", "").strip()
        alt = attributes.get("alt", "").strip()
        placed = None
        if source.startswith("data:"):
            placed = self.images.add_data_uri(source)
        elif source:
            # Anything else would have to be fetched. It is not.
            self.images.dropped_remote += 1
        if placed is None:
            if alt:
                self.out.append(f'<p class="note">[picture: {html.escape(alt, quote=False)}]</p>')
            return
        name, width, height = placed
        self._since_break += MIN_SECTION_CHARS  # a picture is content
        self.out.append(f'<p><img src="{name}" width="{width:.0f}" height="{height:.0f}"></p>')

    def result(self) -> str:
        if self._pre:  # an unclosed <pre> at the end of the file
            text = "".join(self._pre_text).strip("\n")
            self.out.append(html.escape(_wrap_code(text), quote=False) + "</pre>")
        return "".join(self.out)


def clean_html(source: str, images: Images) -> tuple[str, str]:
    """The structure, text and embedded pictures of an HTML page, and its title."""
    cleaner = _Cleaner(images)
    try:
        cleaner.feed(source)
        cleaner.close()
    except Exception as exc:  # html.parser is lenient, but a broken file is still possible
        raise UnreadableNotebookError(f"That HTML could not be read: {exc}") from exc
    return cleaner.result(), " ".join(cleaner.title.split())


# --- Markdown, as much of it as notebooks use --------------------------------------------
_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_NUMBERED = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _inline_text(text: str) -> str:
    """Emphasis, links and pictures in a run of text that holds no inline code."""
    text = text.replace("&", "&amp;")
    # A "<" that does not open a tag is a less-than sign.
    text = re.sub(r"<(?!/?[a-zA-Z!])", "&lt;", text)
    text = _IMAGE.sub(lambda m: f'<img src="{m.group(2)}" alt="{m.group(1)}">', text)
    text = _LINK.sub(lambda m: m.group(1), text)  # the words of a link, not its address
    text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(?=\S)(.+?)(?<=\S)__", r"<b>\1</b>", text)
    return re.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"<i>\1</i>", text)


def _inline(text: str) -> str:
    """Inline Markdown to HTML. Raw HTML tags pass through to the cleaner; text is escaped."""
    parts = re.split(r"(`[^`\n]+`)", text)
    rendered: list[str] = []
    for part in parts:
        if len(part) > 2 and part.startswith("`") and part.endswith("`"):
            rendered.append(f"<code>{html.escape(part[1:-1], quote=False)}</code>")
        else:
            rendered.append(_inline_text(part))
    return "".join(rendered)


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def markdown_to_html(source: str) -> str:
    """Headings, paragraphs, lists, code fences, quotes, tables and inline emphasis."""
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    paragraph: list[str] = []
    lists: list[str] = []  # open list tags, innermost last

    def end_paragraph() -> None:
        if paragraph:
            out.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def end_lists() -> None:
        while lists:
            out.append(f"</li></{lists.pop()}>")

    index = 0
    while index < len(lines):
        line = lines[index]
        fence = _FENCE.match(line)
        if fence:
            end_paragraph()
            end_lists()
            body: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith(fence.group(1)):
                body.append(lines[index])
                index += 1
            index += 1
            out.append(f"<pre>{html.escape(chr(10).join(body), quote=False)}</pre>")
            continue
        if not line.strip():
            end_paragraph()
            end_lists()
            index += 1
            continue
        heading = _HEADING.match(line)
        if heading:
            end_paragraph()
            end_lists()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            index += 1
            continue
        if re.match(r"^\s*([-*_])(\s*\1){2,}\s*$", line):
            end_paragraph()
            end_lists()
            out.append("<hr>")
            index += 1
            continue
        if "|" in line and index + 1 < len(lines) and _TABLE_RULE.match(lines[index + 1]):
            end_paragraph()
            end_lists()
            out.append("<table><tr>")
            out.extend(f"<th>{_inline(cell)}</th>" for cell in _table_cells(line))
            out.append("</tr>")
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                out.append("<tr>")
                out.extend(f"<td>{_inline(cell)}</td>" for cell in _table_cells(lines[index]))
                out.append("</tr>")
                index += 1
            out.append("</table>")
            continue
        bullet = _BULLET.match(line)
        numbered = None if bullet else _NUMBERED.match(line)
        if bullet or numbered:
            end_paragraph()
            tag = "ul" if bullet else "ol"
            text = bullet.group(3) if bullet else numbered.group(2)  # type: ignore[union-attr]
            if not lists:
                out.append(f"<{tag}><li>")
                lists.append(tag)
            else:
                out.append("</li><li>")
            out.append(_inline(text))
            index += 1
            continue
        if line.lstrip().startswith(">"):
            end_paragraph()
            end_lists()
            quoted: list[str] = []
            while index < len(lines) and lines[index].lstrip().startswith(">"):
                quoted.append(lines[index].lstrip()[1:].strip())
                index += 1
            out.append(f"<blockquote><p>{_inline(' '.join(quoted))}</p></blockquote>")
            continue
        if lists and line.startswith(("  ", "\t")):
            out.append(" " + _inline(line.strip()))  # a list item carried onto the next line
            index += 1
            continue
        end_lists()
        paragraph.append(line.strip())
        index += 1
    end_paragraph()
    end_lists()
    return "\n".join(out)


# --- a notebook as HTML ------------------------------------------------------------------
def _text_of(value: Any) -> str:
    if isinstance(value, list):
        return "".join(str(part) for part in value)
    return str(value) if isinstance(value, str) else ""


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _clip_output(text: str) -> str:
    text = _ANSI.sub("", text).replace("\r\n", "\n")
    # A progress bar redraws its line with carriage returns; keep its last state.
    text = "\n".join(line.rsplit("\r", 1)[-1] for line in text.split("\n"))
    lines = text.rstrip("\n").split("\n")
    if len(lines) > MAX_OUTPUT_LINES:
        hidden = len(lines) - MAX_OUTPUT_LINES
        lines = [*lines[: MAX_OUTPUT_LINES - 10], f"… {hidden} more lines …", *lines[-10:]]
    text = "\n".join(lines)
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + "\n… output cut here …"
    return text


def _markdown_cell(cell: dict[str, Any]) -> str:
    source = _text_of(cell.get("source"))
    # A picture pasted into a cell is stored beside it and referred to by name.
    attachments = cell.get("attachments") if isinstance(cell.get("attachments"), dict) else {}

    def attached(match: re.Match[str]) -> str:
        entry = attachments.get(match.group(1))
        if isinstance(entry, dict):
            for mime, payload in entry.items():
                if mime.startswith("image/") and isinstance(payload, str):
                    return f"data:{mime};base64,{payload.strip()}"
        return match.group(0)

    source = re.sub(r"attachment:([^)\s\"']+)", attached, source)
    return markdown_to_html(source)


def _code_cell(cell: dict[str, Any], number: int) -> str:
    parts: list[str] = []
    source = _text_of(cell.get("source")).rstrip()
    if source:
        parts.append(f'<p class="label">Code cell {number}</p>')
        parts.append(f"<pre>{html.escape(source, quote=False)}</pre>")
    for output in cell.get("outputs") or []:
        if not isinstance(output, dict):
            continue
        kind = output.get("output_type")
        if kind == "stream":
            text = _clip_output(_text_of(output.get("text")))
            if text.strip():
                style = "err" if output.get("name") == "stderr" else "out"
                parts.append(f'<pre class="{style}">{html.escape(text, quote=False)}</pre>')
        elif kind == "error":
            trace = output.get("traceback")
            text = "\n".join(trace) if isinstance(trace, list) else ""
            text = text or f"{output.get('ename', 'Error')}: {output.get('evalue', '')}"
            parts.append(f'<pre class="err">{html.escape(_clip_output(text), quote=False)}</pre>')
        elif kind in ("display_data", "execute_result"):
            data = output.get("data") if isinstance(output.get("data"), dict) else {}
            image = next(
                (mime for mime in ("image/png", "image/jpeg", "image/gif") if mime in data), None
            )
            if image:
                payload = _text_of(data[image]).strip()
                parts.append(f'<img src="data:{image};base64,{payload}" alt="output figure">')
            elif "text/markdown" in data:
                parts.append(markdown_to_html(_text_of(data["text/markdown"])))
            elif "text/plain" in data:
                text = _clip_output(_text_of(data["text/plain"]))
                if text.strip():
                    parts.append(f'<pre class="out">{html.escape(text, quote=False)}</pre>')
    return "\n".join(parts)


def notebook_to_html(data: bytes) -> tuple[str, int]:
    """A notebook's cells as HTML, in order, and how many cells there were."""
    try:
        notebook = json.loads(data.decode("utf-8-sig", errors="replace"))
    except ValueError as exc:
        raise UnreadableNotebookError("That file is not a readable Jupyter notebook.") from exc
    if not isinstance(notebook, dict):
        raise UnreadableNotebookError("That file is not a Jupyter notebook.")
    cells = notebook.get("cells")
    if cells is None and isinstance(notebook.get("worksheets"), list):  # the old v3 layout
        cells = [
            cell
            for sheet in notebook["worksheets"]
            if isinstance(sheet, dict)
            for cell in sheet.get("cells") or []
        ]
    if not isinstance(cells, list):
        raise UnreadableNotebookError("That file is not a Jupyter notebook: it has no cells.")

    parts: list[str] = []
    code_number = 0
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        kind = cell.get("cell_type")
        if kind == "markdown":
            parts.append(_markdown_cell(cell))
        elif kind == "code":
            code_number += 1
            # Format 3 called the source "input".
            code = cell if "source" in cell else {**cell, "source": cell.get("input", "")}
            parts.append(_code_cell(code, code_number))
        elif kind == "raw":
            text = _text_of(cell.get("source")).strip()
            if text:
                parts.append(f"<pre>{html.escape(text, quote=False)}</pre>")
        elif kind == "heading":  # v3
            level = min(max(int(cell.get("level", 1) or 1), 1), 6)
            text = html.escape(_text_of(cell.get("source")), quote=False)
            parts.append(f"<h{level}>{text}</h{level}>")
    return "\n".join(part for part in parts if part), len(cells)


# --- layout ------------------------------------------------------------------------------
def _layout(body: str, images: Images, *, max_pages: int) -> tuple[bytes, int, bool]:
    """Flow clean HTML onto pages. Returns the PDF, its page count and whether it was cut."""
    archive = pymupdf.Archive()
    for name, data in images.items.items():
        archive.add(data, name)
    story = pymupdf.Story(html=f"<body>{body}</body>", user_css=_CSS, archive=archive)
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    where = pymupdf.Rect(_MARGIN, _MARGIN, _PAGE.width - _MARGIN, _PAGE.height - _MARGIN)
    pages = 0
    more = True
    while more and pages < max_pages:
        device = writer.begin_page(_PAGE)
        more, _filled = story.place(where)
        story.draw(device)
        writer.end_page()
        pages += 1
    writer.close()
    # The writer stores pictures as it received them; saving again deflates them.
    with pymupdf.open(stream=buffer.getvalue(), filetype="pdf") as document:
        data = document.tobytes(garbage=3, deflate=True, deflate_images=True)
    return data, pages, bool(more)


def _render(body: str, images: Images, *, title: str, max_slides: int, what: str) -> RenderedDeck:
    if not re.sub(r"<[^>]+>", "", body).strip() and not images.items:
        raise UnreadableNotebookError(f"That {what} has no text or pictures in it.")
    try:
        pdf, pages, cut = _layout(body, images, max_pages=max_slides)
    except UnreadableNotebookError:
        raise
    except Exception as exc:
        log.exception("notebook_layout_failed")
        raise UnreadableNotebookError(f"That {what} could not be laid out as pages.") from exc
    if pages == 0:
        raise UnreadableNotebookError(f"That {what} has nothing in it to show.")

    warnings: list[str] = []
    if cut:
        warnings.append(f"Only the first {pages} pages of this {what} were laid out.")
    if images.dropped_remote:
        count = images.dropped_remote
        warnings.append(
            f"{count} picture{'s' if count != 1 else ''} linked from the web "
            f"{'were' if count != 1 else 'was'} left out: files are never fetched from the "
            "internet when one is opened."
        )
    if images.dropped_bad:
        count = images.dropped_bad
        warnings.append(f"{count} picture{'s' if count != 1 else ''} could not be read.")
    return RenderedDeck(pdf=pdf, slide_count=pages, title=title, warnings=warnings)


def render_notebook_to_pdf(path: Path, *, max_slides: int) -> RenderedDeck:
    """A Jupyter notebook as a PDF: its text, its code and the outputs saved in it."""
    raw_html, _cells = notebook_to_html(Path(path).read_bytes())
    images = Images()
    body, _ = clean_html(raw_html, images)
    first_heading = re.search(r"<h[1-3][^>]*>(.*?)</h[1-3]>", body, re.DOTALL)
    title = html.unescape(re.sub(r"<[^>]+>", "", first_heading.group(1))) if first_heading else ""
    return _render(
        body, images, title=" ".join(title.split())[:200], max_slides=max_slides, what="notebook"
    )


def render_html_to_pdf(path: Path, *, max_slides: int) -> RenderedDeck:
    """An HTML page as a PDF: its structure, text and embedded pictures, nothing run."""
    data = Path(path).read_bytes()
    declared = re.search(rb"<meta[^>]+charset=[\"']?([a-zA-Z0-9_-]+)", data[:4096])
    encoding = declared.group(1).decode("ascii", errors="ignore") if declared else "utf-8"
    try:
        source = data.decode(encoding, errors="replace")
    except LookupError:
        source = data.decode("utf-8", errors="replace")
    images = Images()
    body, title = clean_html(source, images)
    return _render(body, images, title=title[:200], max_slides=max_slides, what="HTML page")
