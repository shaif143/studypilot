"""Old-format PowerPoint (.ppt, 1997-2003): read the binary file, render a PDF.

A lecturer's archive is full of these, and nothing a Mac app can bundle
converts them — PowerPoint and LibreOffice are the only faithful readers.
What the student needs is the same as for a .pptx (see `pptx.py`): the words
on each slide, the pictures, each where it was, so the reader can show the
slide and a citation can point at it. The binary format carries exactly
that, in a documented layout (MS-PPT), and reading it needs nothing beyond
`olefile` for the compound-file container and the record walker below.

How the file is laid out, as far as this reader needs:

* An OLE compound file with a ``PowerPoint Document`` stream (the records),
  a ``Current User`` stream (which edit is current), and a ``Pictures``
  stream (the images, as OfficeArt BLIPs, referenced by index).
* The records form a tree: 8-byte headers (version/instance, type, length),
  containers holding atoms. The file is append-only across saves, so the
  current ``UserEditAtom`` (found from ``Current User``) and its chain of
  ``PersistDirectoryAtom`` records say where the live ``Document``, each
  ``Slide`` and each ``Notes`` container begins. Everything else in the
  stream is a stale edit and is ignored.
* Placeholder text (titles, bodies) lives in the document's
  ``SlideListWithText``, grouped under a ``SlidePersistAtom`` per slide, in
  slide order. A slide's shapes refer to it by index (``OutlineTextRefAtom``).
  Free text boxes carry their text inline. Notes have their own list.
* Shapes are OfficeArt (Escher) records inside the slide's ``PPDrawing``:
  an anchor in master units (576 per inch), properties (``pib`` names a
  picture), placeholder and text records. Groups nest with their own
  coordinate space.

What is lost is what `pptx.py` loses too — theme, fonts, animations — plus
pictures stored as Windows metafiles (EMF/WMF), which nothing here can
rasterise; those are reported, not silently dropped.
"""

from __future__ import annotations

import logging
import re
import struct
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from app.ingestion.pptx import (
    _NOTES_STRIP_POINTS,
    RenderedDeck,
    UnreadablePresentationError,
    _draw_notes,
    _insert_shrinking,
    _inset,
)

log = logging.getLogger(__name__)

PPT_MEDIA_TYPE = "application/vnd.ms-powerpoint"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
#: Master units per PDF point: 576 per inch over 72 per inch.
_MASTER_PER_POINT = 8.0
_ENCRYPTED_TOKEN = 0xF3D1C4DF

# --- record types (MS-PPT) ------------------------------------------------------
RT_DOCUMENT = 0x03E8
RT_DOCUMENT_ATOM = 0x03E9
RT_SLIDE = 0x03EE
RT_SLIDE_ATOM = 0x03EF
RT_NOTES = 0x03F0
RT_SLIDE_PERSIST_ATOM = 0x03F3
RT_PP_DRAWING = 0x040C
RT_OE_PLACEHOLDER_ATOM = 0x0BC3
RT_OUTLINE_TEXT_REF_ATOM = 0x0F9E
RT_TEXT_HEADER_ATOM = 0x0F9F
RT_TEXT_CHARS_ATOM = 0x0FA0
RT_TEXT_BYTES_ATOM = 0x0FA8
RT_SLIDE_LIST_WITH_TEXT = 0x0FF0
RT_USER_EDIT_ATOM = 0x0FF5
RT_CURRENT_USER_ATOM = 0x0FF6
RT_PERSIST_DIRECTORY_ATOM = 0x1772
# OfficeArt (Escher)
ESC_DG_CONTAINER = 0xF002
ESC_SPGR_CONTAINER = 0xF003
ESC_SP_CONTAINER = 0xF004
ESC_SPGR_ATOM = 0xF009
ESC_SP_ATOM = 0xF00A
ESC_OPT = 0xF00B
ESC_CHILD_ANCHOR = 0xF00F
ESC_CLIENT_ANCHOR = 0xF010
ESC_CLIENT_DATA = 0xF011
ESC_CLIENT_TEXTBOX = 0xF00D
ESC_BLIP_EMF = 0xF01A
ESC_BLIP_WMF = 0xF01B
ESC_BLIP_PICT = 0xF01C
ESC_BLIP_JPEG = 0xF01D
ESC_BLIP_PNG = 0xF01E
ESC_BLIP_DIB = 0xF01F
ESC_BLIP_TIFF = 0xF029
#: Property id of the picture a shape shows (1-based index into Pictures).
PID_PIB = 0x0104
#: Text header types that are a slide's title.
_TITLE_TYPES = {0, 6}
_NOTES_TYPE = 2

_CONTROL = re.compile(r"[\x00-\x08\x0c\x0e-\x1f]")


def looks_like_ppt(data: bytes, filename: str = "") -> bool:
    """An OLE compound file: the only format a .ppt (or .pot, .pps) ever is."""
    if not data.startswith(_OLE_MAGIC):
        return False
    try:
        import olefile

        with olefile.OleFileIO(data) as ole:
            return ole.exists("PowerPoint Document")
    except Exception:
        # A damaged container is refused here, with a message, rather than
        # accepted on its name and failed later.
        return False


# --- records ------------------------------------------------------------------
@dataclass
class Record:
    type: int
    instance: int
    version: int
    #: Offset of the 8-byte header in the stream.
    offset: int
    body: memoryview

    @property
    def is_container(self) -> bool:
        return self.version == 0xF

    def children(self) -> Iterator[Record]:
        if self.is_container:
            yield from iter_records(self.body, base=self.offset + 8)

    def find(self, record_type: int, instance: int | None = None) -> Record | None:
        for child in self.children():
            if child.type == record_type and (instance is None or child.instance == instance):
                return child
        return None

    def find_all(self, record_type: int) -> list[Record]:
        return [child for child in self.children() if child.type == record_type]


def iter_records(buffer: memoryview | bytes, base: int = 0) -> Iterator[Record]:
    """Walk a sequence of records; a truncated tail ends the walk quietly."""
    view = memoryview(buffer)
    offset = 0
    end = len(view)
    while offset + 8 <= end:
        verinst, rtype, rlen = struct.unpack_from("<HHI", view, offset)
        if offset + 8 + rlen > end:
            rlen = end - offset - 8
        yield Record(
            type=rtype,
            instance=verinst >> 4,
            version=verinst & 0xF,
            offset=base + offset,
            body=view[offset + 8 : offset + 8 + rlen],
        )
        offset += 8 + rlen


def record_at(stream: memoryview, offset: int) -> Record | None:
    if offset < 0 or offset + 8 > len(stream):
        return None
    return next(iter_records(stream[offset:], base=offset), None)


# --- text --------------------------------------------------------------------------
def _decode_text(record: Record) -> str:
    raw = bytes(record.body)
    if record.type == RT_TEXT_CHARS_ATOM:
        text = raw.decode("utf-16-le", errors="replace")
    else:
        # TextBytesAtom: each byte is the low half of a UTF-16 code unit.
        text = raw.decode("latin-1")
    text = text.replace("\r", "\n").replace("\x0b", "\n")
    return _CONTROL.sub("", text).strip()


@dataclass
class TextBlock:
    kind: int  # TextHeaderAtom type
    text: str

    @property
    def is_title(self) -> bool:
        return self.kind in _TITLE_TYPES


def _text_blocks(container: Record) -> list[TextBlock]:
    """TextHeaderAtom + the text atom that follows, in order."""
    blocks: list[TextBlock] = []
    kind = 4
    for child in container.children():
        if child.type == RT_TEXT_HEADER_ATOM and len(child.body) >= 4:
            kind = struct.unpack_from("<I", child.body)[0]
        elif child.type in (RT_TEXT_CHARS_ATOM, RT_TEXT_BYTES_ATOM):
            blocks.append(TextBlock(kind=kind, text=_decode_text(child)))
            kind = 4
    return blocks


# --- the persist directory --------------------------------------------------------
def _current_edit_offset(current_user: bytes) -> int:
    record = record_at(memoryview(current_user), 0)
    if record is None or record.type != RT_CURRENT_USER_ATOM or len(record.body) < 12:
        raise UnreadablePresentationError("The Current User stream is not readable.")
    _size, token, offset = struct.unpack_from("<III", record.body)
    if token == _ENCRYPTED_TOKEN:
        raise UnreadablePresentationError(
            "This presentation is password-protected; remove the password and upload again."
        )
    return offset


def _persist_offsets(stream: memoryview, current_user: bytes) -> tuple[dict[int, int], int]:
    """persistId → stream offset, and the document's persist id.

    Follows the edit chain backwards, then applies directories oldest first so
    the newest entry for a persist id wins — that is how incremental saves
    supersede earlier content.
    """
    directories: list[Record] = []
    doc_persist = 0
    offset = _current_edit_offset(current_user)
    seen: set[int] = set()
    while offset and offset not in seen:
        seen.add(offset)
        edit = record_at(stream, offset)
        if edit is None or edit.type != RT_USER_EDIT_ATOM or len(edit.body) < 20:
            break
        _last_slide, _ver, _minor, _major, last_edit, dir_offset, doc_ref = struct.unpack_from(
            "<IHBBIII", edit.body
        )
        if not doc_persist:
            doc_persist = doc_ref
        directory = record_at(stream, dir_offset)
        if directory is not None and directory.type == RT_PERSIST_DIRECTORY_ATOM:
            directories.append(directory)
        offset = last_edit
    offsets: dict[int, int] = {}
    for directory in reversed(directories):
        body = directory.body
        pos = 0
        while pos + 4 <= len(body):
            head = struct.unpack_from("<I", body, pos)[0]
            pos += 4
            first_id = head & 0xFFFFF
            count = head >> 20
            for index in range(count):
                if pos + 4 > len(body):
                    break
                offsets[first_id + index] = struct.unpack_from("<I", body, pos)[0]
                pos += 4
    if not offsets:
        raise UnreadablePresentationError("The presentation's record directory is missing.")
    return offsets, doc_persist


# --- shapes ---------------------------------------------------------------------------
Transform = Callable[[float, float], tuple[float, float]]


@dataclass
class Shape:
    rect: pymupdf.Rect
    text: str = ""
    is_title: bool = False
    is_body: bool = False
    picture: int = 0  # 1-based BLIP index, 0 for none


@dataclass
class SlideContent:
    number: int
    shapes: list[Shape] = field(default_factory=list)
    notes: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        for shape in self.shapes:
            if shape.is_title and shape.text:
                return shape.text.splitlines()[0].strip()
        return ""


def _anchor_rect(record: Record) -> tuple[float, float, float, float] | None:
    body = record.body
    if record.type == ESC_CLIENT_ANCHOR:
        if len(body) >= 16:
            top, left, right, bottom = struct.unpack_from("<iiii", body)
        elif len(body) >= 8:
            top, left, right, bottom = struct.unpack_from("<hhhh", body)
        else:
            return None
        return left, top, right, bottom
    if record.type == ESC_CHILD_ANCHOR and len(body) >= 16:
        left, top, right, bottom = struct.unpack_from("<iiii", body)
        return left, top, right, bottom
    return None


def _properties(record: Record) -> dict[int, int]:
    """The fixed-size part of an OfficeArt property table."""
    out: dict[int, int] = {}
    body = record.body
    for index in range(record.instance):
        pos = index * 6
        if pos + 6 > len(body):
            break
        opid, value = struct.unpack_from("<HI", body, pos)
        out[opid & 0x3FFF] = value
    return out


def _shape_from(container: Record, transform: Transform, outline: list[TextBlock]) -> Shape | None:
    anchor = None
    picture = 0
    text = ""
    kind = 4
    for child in container.children():
        if child.type in (ESC_CLIENT_ANCHOR, ESC_CHILD_ANCHOR):
            anchor = _anchor_rect(child)
        elif child.type == ESC_OPT:
            picture = _properties(child).get(PID_PIB, 0)
        elif child.type == ESC_CLIENT_TEXTBOX:
            ref = child.find(RT_OUTLINE_TEXT_REF_ATOM)
            if ref is not None and len(ref.body) >= 4:
                index = struct.unpack_from("<I", ref.body)[0]
                if 0 <= index < len(outline):
                    text, kind = outline[index].text, outline[index].kind
            else:
                blocks = _text_blocks(child)
                if blocks:
                    text = "\n".join(block.text for block in blocks if block.text)
                    kind = blocks[0].kind
    if anchor is None or (not text and not picture):
        return None
    left, top, right, bottom = anchor
    x0, y0 = transform(left, top)
    x1, y1 = transform(right, bottom)
    rect = pymupdf.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    if rect.is_empty:
        return None
    return Shape(
        rect=rect,
        text=text,
        is_title=kind in _TITLE_TYPES,
        is_body=kind not in _TITLE_TYPES and kind != _NOTES_TYPE,
        picture=picture,
    )


def _walk_group(group: Record, transform: Transform, outline: list[TextBlock]) -> list[Shape]:
    """Shapes of a SpgrContainer; nested groups get their own coordinate space."""
    shapes: list[Shape] = []
    children = list(group.children())
    if not children:
        return shapes
    # The first child is the group's own shape: its anchor places the group,
    # its SpgrAtom is the space the other children are drawn in.
    head = children[0]
    inner = transform
    if head.type == ESC_SP_CONTAINER:
        spgr = head.find(ESC_SPGR_ATOM)
        anchor_record = head.find(ESC_CLIENT_ANCHOR) or head.find(ESC_CHILD_ANCHOR)
        anchor = _anchor_rect(anchor_record) if anchor_record is not None else None
        if spgr is not None and len(spgr.body) >= 16 and anchor is not None:
            gl, gt, gr, gb = struct.unpack_from("<iiii", spgr.body)
            left, top, right, bottom = anchor
            px0, py0 = transform(left, top)
            px1, py1 = transform(right, bottom)
            gw = (gr - gl) or 1
            gh = (gb - gt) or 1

            def inner(x: float, y: float, _gl=gl, _gt=gt, _gw=gw, _gh=gh, _p=(px0, py0, px1, py1)):
                return (
                    _p[0] + (x - _gl) / _gw * (_p[2] - _p[0]),
                    _p[1] + (y - _gt) / _gh * (_p[3] - _p[1]),
                )

    for child in children[1:]:
        if child.type == ESC_SPGR_CONTAINER:
            shapes.extend(_walk_group(child, inner, outline))
        elif child.type == ESC_SP_CONTAINER:
            shape = _shape_from(child, inner, outline)
            if shape is not None:
                shapes.append(shape)
    return shapes


def _slide_shapes(slide: Record, outline: list[TextBlock]) -> list[Shape]:
    drawing = slide.find(RT_PP_DRAWING)
    if drawing is None:
        return []
    dg = drawing.find(ESC_DG_CONTAINER)
    if dg is None:
        return []

    def master_units(x: float, y: float) -> tuple[float, float]:
        return x / _MASTER_PER_POINT, y / _MASTER_PER_POINT

    shapes: list[Shape] = []
    for child in dg.children():
        if child.type == ESC_SPGR_CONTAINER:
            # The slide's own group ("patriarch"): its children are anchored
            # in master units directly, so it carries no transform. Only a
            # group *inside* it has a coordinate space of its own.
            for member in list(child.children())[1:]:
                if member.type == ESC_SPGR_CONTAINER:
                    shapes.extend(_walk_group(member, master_units, outline))
                elif member.type == ESC_SP_CONTAINER:
                    shape = _shape_from(member, master_units, outline)
                    if shape is not None:
                        shapes.append(shape)
        elif child.type == ESC_SP_CONTAINER:
            shape = _shape_from(child, master_units, outline)
            if shape is not None:
                shapes.append(shape)
    return shapes


# --- pictures -------------------------------------------------------------------------
@dataclass
class Picture:
    data: bytes
    kind: str  # "image" (decodable) | "metafile" | "unknown"


def _blips(pictures: bytes) -> list[Picture]:
    """The Pictures stream, in order; `pib` indexes it from 1."""
    found: list[Picture] = []
    for record in iter_records(pictures):
        body = bytes(record.body)
        if record.type in (ESC_BLIP_JPEG, ESC_BLIP_PNG, ESC_BLIP_DIB, ESC_BLIP_TIFF):
            # One 16-byte UID, or two for the "has secondary UID" instances.
            two_uids = record.instance in {0x46B, 0x6E3, 0x6E1, 0x7A9, 0x6E5}
            start = 16 * (2 if two_uids else 1) + 1  # + the tag byte
            data = body[start:]
            if record.type == ESC_BLIP_DIB and data[:4] != b"BM":
                # A DIB is a BMP without its 14-byte file header; add one.
                data = b"BM" + struct.pack("<IHHI", 14 + len(data), 0, 0, 14 + 40) + data
            found.append(Picture(data=data, kind="image"))
        elif record.type in (ESC_BLIP_EMF, ESC_BLIP_WMF, ESC_BLIP_PICT):
            found.append(Picture(data=body, kind="metafile"))
        else:
            found.append(Picture(data=body, kind="unknown"))
    return found


def _draw_picture(page: pymupdf.Page, rect: pymupdf.Rect, picture: Picture) -> bool:
    if picture.kind != "image":
        return False
    data = picture.data
    try:
        page.insert_image(rect, stream=data)
        return True
    except Exception:
        pass
    # Some writers zlib-compress the payload; try that before giving up.
    try:
        page.insert_image(rect, stream=zlib.decompress(data))
        return True
    except Exception:
        return False


# --- the deck --------------------------------------------------------------------------
@dataclass
class ParsedDeck:
    width: float
    height: float
    slides: list[SlideContent]
    pictures: list[Picture]


def parse_streams(document: bytes, current_user: bytes, pictures: bytes = b"") -> ParsedDeck:
    """Read the three streams into slides; the entry point the tests drive."""
    stream = memoryview(document)
    offsets, doc_persist = _persist_offsets(stream, current_user)
    doc = record_at(stream, offsets.get(doc_persist, 0))
    if doc is None or doc.type != RT_DOCUMENT:
        # Older writers put the document first and omit the reference.
        doc = record_at(stream, 0)
    if doc is None or doc.type != RT_DOCUMENT:
        raise UnreadablePresentationError("The presentation has no document record.")

    width, height = 720.0, 540.0
    atom = doc.find(RT_DOCUMENT_ATOM)
    if atom is not None and len(atom.body) >= 8:
        sx, sy = struct.unpack_from("<ii", atom.body)
        if sx > 0 and sy > 0:
            width, height = sx / _MASTER_PER_POINT, sy / _MASTER_PER_POINT

    # Slide order and placeholder text; notes text keyed by the notes' id.
    slide_entries: list[tuple[int, int, list[TextBlock]]] = []  # (persist, slideId, texts)
    notes_text: dict[int, str] = {}
    for listing in doc.find_all(RT_SLIDE_LIST_WITH_TEXT):
        current: tuple[int, int, list[TextBlock]] | None = None
        kind = 4
        for child in listing.children():
            if child.type == RT_SLIDE_PERSIST_ATOM and len(child.body) >= 16:
                persist, _flags, _count, slide_id = struct.unpack_from("<IIII", child.body)
                current = (persist, slide_id, [])
                if listing.instance == 0:
                    slide_entries.append(current)
                elif listing.instance == 2:
                    notes_text[slide_id] = ""
            elif child.type == RT_TEXT_HEADER_ATOM and len(child.body) >= 4:
                kind = struct.unpack_from("<I", child.body)[0]
            elif child.type in (RT_TEXT_CHARS_ATOM, RT_TEXT_BYTES_ATOM) and current is not None:
                text = _decode_text(child)
                if listing.instance == 0:
                    current[2].append(TextBlock(kind=kind, text=text))
                elif listing.instance == 2 and text:
                    joined = notes_text.get(current[1], "")
                    notes_text[current[1]] = f"{joined}\n{text}".strip()
                kind = 4

    slides: list[SlideContent] = []
    for number, (persist, _slide_id, outline) in enumerate(slide_entries, start=1):
        content = SlideContent(number=number)
        slide = record_at(stream, offsets.get(persist, -1))
        if slide is None or slide.type != RT_SLIDE:
            # No drawing to place things on: keep the words at least.
            content.warnings.append(f"Slide {number}: layout missing; text placed by default.")
            content.shapes = _default_layout(outline, width, height)
            slides.append(content)
            continue
        content.shapes = _tidy(_slide_shapes(slide, outline), width, height)
        placed = {shape.text for shape in content.shapes if shape.text}
        missing = [block for block in outline if block.text and block.text not in placed]
        if missing:
            # Placeholder text whose shape could not be found still counts.
            content.shapes.extend(_default_layout(missing, width, height))
        atom = slide.find(RT_SLIDE_ATOM)
        if atom is not None and len(atom.body) >= 20:
            notes_id = struct.unpack_from("<I", atom.body, 16)[0]
            content.notes = notes_text.get(notes_id, "")
        slides.append(content)

    if not slides:
        raise UnreadablePresentationError("This presentation has no slides.")
    return ParsedDeck(width=width, height=height, slides=slides, pictures=_blips(pictures))


def _tidy(shapes: list[Shape], width: float, height: float) -> list[Shape]:
    """Drop field markers, keep text on the page, and name a title if none is.

    Decks laid out by hand have no placeholder of type "title"; the top text
    box is the title to a reader, so it is drawn as one. A slide-number field
    is stored as a lone "*", which is noise on a page.
    """
    kept: list[Shape] = []
    for shape in shapes:
        if shape.text.strip() in {"*", "‹#›"} and not shape.picture:
            continue
        if shape.text:
            # A box dragged past the slide's edge would lose its first letters.
            shape.rect = pymupdf.Rect(
                max(shape.rect.x0, 0.0),
                max(shape.rect.y0, 0.0),
                min(shape.rect.x1, width),
                min(shape.rect.y1, height),
            )
            if shape.rect.is_empty:
                shape.rect = pymupdf.Rect(0, 0, width, height * 0.2)
        kept.append(shape)
    if not any(shape.is_title for shape in kept):
        candidates = [
            shape
            for shape in kept
            if shape.text
            and shape.rect.y0 < height * 0.35
            and shape.rect.height <= height * 0.3
            and len(shape.text) <= 120
        ]
        if candidates:
            top = min(candidates, key=lambda shape: shape.rect.y0)
            top.is_title, top.is_body = True, False
    return kept


def _default_layout(blocks: list[TextBlock], width: float, height: float) -> list[Shape]:
    """Title at the top, everything else below, when no shape places it."""
    shapes: list[Shape] = []
    titles = [b for b in blocks if b.is_title and b.text]
    bodies = [b for b in blocks if not b.is_title and b.text]
    top = height * 0.06
    if titles:
        shapes.append(
            Shape(
                rect=pymupdf.Rect(width * 0.06, top, width * 0.94, height * 0.22),
                text="\n".join(t.text for t in titles),
                is_title=True,
            )
        )
        top = height * 0.26
    if bodies:
        shapes.append(
            Shape(
                rect=pymupdf.Rect(width * 0.06, top, width * 0.94, height * 0.94),
                text="\n".join(b.text for b in bodies),
                is_body=True,
            )
        )
    return shapes


def _bulleted(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    if len([line for line in lines if line.strip()]) < 2:
        return text
    return "\n".join(f"• {line.strip()}" if line.strip() else "" for line in lines)


def render_ppt_to_pdf(path: Path, *, max_slides: int) -> RenderedDeck:
    try:
        import olefile
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise UnreadablePresentationError("olefile is not installed.") from exc
    try:
        with olefile.OleFileIO(str(path)) as ole:
            if not ole.exists("PowerPoint Document"):
                raise UnreadablePresentationError(
                    "This file is not a PowerPoint presentation (no slide data inside)."
                )
            document = ole.openstream("PowerPoint Document").read()
            current_user = (
                ole.openstream("Current User").read() if ole.exists("Current User") else b""
            )
            pictures = ole.openstream("Pictures").read() if ole.exists("Pictures") else b""
    except UnreadablePresentationError:
        raise
    except Exception as exc:
        raise UnreadablePresentationError(f"Not a readable PowerPoint file: {exc}") from exc
    deck = parse_streams(document, current_user, pictures)
    return render_parsed(deck, max_slides=max_slides, fallback_title=path.stem)


def render_parsed(deck: ParsedDeck, *, max_slides: int, fallback_title: str = "") -> RenderedDeck:
    warnings: list[str] = []
    limit = min(len(deck.slides), max_slides)
    if len(deck.slides) > limit:
        warnings.append(f"Only the first {limit} of {len(deck.slides)} slides were processed.")

    pdf = pymupdf.open()
    title = ""
    metafiles = 0
    for content in deck.slides[:limit]:
        page = pdf.new_page(
            width=deck.width, height=deck.height + (_NOTES_STRIP_POINTS if content.notes else 0)
        )
        warnings.extend(content.warnings)
        drew_anything = False
        for shape in content.shapes:
            if shape.picture:
                picture = (
                    deck.pictures[shape.picture - 1]
                    if 0 < shape.picture <= len(deck.pictures)
                    else None
                )
                if picture is None:
                    continue
                if _draw_picture(page, shape.rect, picture):
                    drew_anything = True
                elif picture.kind == "metafile":
                    metafiles += 1
                    page.draw_rect(shape.rect, color=(0.8, 0.8, 0.8), width=0.5)
                    _insert_shrinking(page, _inset(shape.rect, 4), "[drawing not rendered]", 8)
                else:
                    warnings.append(f"Slide {content.number}: a picture could not be decoded.")
            if shape.text:
                size = 24.0 if shape.is_title else 14.0
                if shape.rect.height < 24:
                    size = min(size, 10.0)
                text = _bulleted(shape.text) if shape.is_body else shape.text
                _insert_shrinking(page, _inset(shape.rect, 2), text, size, bold=shape.is_title)
                drew_anything = True
        if not drew_anything:
            warnings.append(f"Slide {content.number} has no text or pictures that could be read.")
        if content.notes:
            _draw_notes(page, content.notes, top=deck.height, width=deck.width)
        if not title:
            title = content.title
    if metafiles:
        warnings.append(
            f"{metafiles} drawing{'s' if metafiles != 1 else ''} in Windows metafile format "
            "could not be rendered; the slide text is intact."
        )

    pdf.set_metadata({"title": title or fallback_title, "producer": "StudyPilot olefile+pymupdf"})
    data = pdf.tobytes(garbage=3, deflate=True)
    pdf.close()
    return RenderedDeck(pdf=data, slide_count=limit, title=title, warnings=warnings)
