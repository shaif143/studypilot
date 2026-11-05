"""Old-format PowerPoint (.ppt) import.

The decks are built record by record here, so each test says exactly what
is in the file: the persist directory, placeholder text in the slide list,
inline text boxes, a picture, a group with its own coordinate space, speaker
notes, an incremental save. A real compound file is written around them
(`cfb_writer`) for the tests that go through the API. A lecturer's own deck
can be run too: set STUDYPILOT_TEST_PPT to its path.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pymupdf
import pytest
from cfb_writer import write_compound_file
from fastapi.testclient import TestClient

from app.config import Settings
from app.ingestion import ppt
from app.ingestion.pipeline import UnsupportedFileError, validate_upload
from app.ingestion.pptx import UnreadablePresentationError


# --- record builders --------------------------------------------------------------
def atom(rtype: int, body: bytes, instance: int = 0) -> bytes:
    return struct.pack("<HHI", (instance << 4), rtype, len(body)) + body


def container(rtype: int, children: list[bytes], instance: int = 0) -> bytes:
    body = b"".join(children)
    return struct.pack("<HHI", (instance << 4) | 0xF, rtype, len(body)) + body


def chars(text: str) -> bytes:
    return atom(ppt.RT_TEXT_CHARS_ATOM, text.encode("utf-16-le"))


def latin(text: str) -> bytes:
    return atom(ppt.RT_TEXT_BYTES_ATOM, text.encode("latin-1"))


def header(kind: int) -> bytes:
    return atom(ppt.RT_TEXT_HEADER_ATOM, struct.pack("<I", kind))


def persist_atom(persist: int, slide_id: int, texts: int = 0) -> bytes:
    return atom(ppt.RT_SLIDE_PERSIST_ATOM, struct.pack("<IIIII", persist, 0, texts, slide_id, 0))


def anchor(left: int, top: int, right: int, bottom: int) -> bytes:
    return atom(ppt.ESC_CLIENT_ANCHOR, struct.pack("<hhhh", top, left, right, bottom))


def child_anchor(left: int, top: int, right: int, bottom: int) -> bytes:
    return atom(ppt.ESC_CHILD_ANCHOR, struct.pack("<iiii", left, top, right, bottom))


def sp(shape_id: int, flags: int = 0xA00) -> bytes:
    return atom(ppt.ESC_SP_ATOM, struct.pack("<II", shape_id, flags))


def group_head(left: int, top: int, right: int, bottom: int, placement: bytes) -> bytes:
    return container(
        ppt.ESC_SP_CONTAINER,
        [
            atom(ppt.ESC_SPGR_ATOM, struct.pack("<iiii", left, top, right, bottom)),
            sp(1, 5),
            placement,
        ],
    )


def outline_ref(index: int) -> bytes:
    return container(
        ppt.ESC_CLIENT_TEXTBOX, [atom(ppt.RT_OUTLINE_TEXT_REF_ATOM, struct.pack("<I", index))]
    )


def inline_text(kind: int, text: str) -> bytes:
    return container(ppt.ESC_CLIENT_TEXTBOX, [header(kind), chars(text)])


def picture_opt(index: int) -> bytes:
    return atom(ppt.ESC_OPT, struct.pack("<HI", 0x4000 | ppt.PID_PIB, index), instance=1)


def shape(*parts: bytes) -> bytes:
    return container(ppt.ESC_SP_CONTAINER, [sp(2), *parts])


def drawing(*shapes: bytes) -> bytes:
    return container(
        ppt.RT_PP_DRAWING,
        [
            container(
                ppt.ESC_DG_CONTAINER,
                [
                    container(
                        ppt.ESC_SPGR_CONTAINER,
                        [group_head(0, 0, 0, 0, anchor(0, 0, 0, 0)), *shapes],
                    )
                ],
            )
        ],
    )


def slide_container(notes_id: int, *shapes: bytes) -> bytes:
    slide_atom = atom(ppt.RT_SLIDE_ATOM, b"\0" * 12 + struct.pack("<II", 0, notes_id) + b"\0" * 4)
    return container(ppt.RT_SLIDE, [slide_atom, drawing(*shapes)])


def png_bytes() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 24, 24), False)
    pix.set_rect(pix.irect, (200, 30, 30))
    return pix.tobytes("png")


class DeckBuilder:
    """Assembles the three streams; the persist directory is written last."""

    def __init__(self) -> None:
        self.document = b""
        self.offsets: dict[int, int] = {}
        self.edits: list[tuple[dict[int, int], int]] = []  # (directory, doc persist)
        self.pictures = b""

    def add(self, persist: int, record: bytes) -> int:
        offset = len(self.document)
        self.document += record
        self.offsets[persist] = offset
        return offset

    def add_picture_png(self) -> None:
        self.pictures += atom(ppt.ESC_BLIP_PNG, b"\0" * 16 + b"\xff" + png_bytes(), instance=0x6E0)

    def commit_edit(self, directory: dict[int, int], doc_persist: int = 1) -> None:
        """Append a persist directory and a UserEditAtom chained to the last one."""
        entries = b""
        for persist, offset in sorted(directory.items()):
            entries += struct.pack("<I", (1 << 20) | persist) + struct.pack("<I", offset)
        dir_offset = len(self.document)
        self.document += atom(ppt.RT_PERSIST_DIRECTORY_ATOM, entries)
        last_edit = self.last_edit_offset
        self.last_edit_offset = len(self.document)
        self.document += atom(
            ppt.RT_USER_EDIT_ATOM,
            struct.pack("<IHBBIIIIHH", 256, 0, 0, 3, last_edit, dir_offset, doc_persist, 9, 0, 0),
        )

    last_edit_offset = 0

    def current_user(self, *, encrypted: bool = False) -> bytes:
        token = ppt._ENCRYPTED_TOKEN if encrypted else 0xE391C05F
        body = struct.pack("<III", 0x14, token, self.last_edit_offset)
        body += struct.pack("<HHBBH", 0, 0x03F4, 3, 0, 0)
        return atom(ppt.RT_CURRENT_USER_ATOM, body)

    def streams(self) -> dict[str, bytes]:
        return {
            "PowerPoint Document": self.document,
            "Current User": self.current_user(),
            "Pictures": self.pictures,
        }


def build_deck() -> DeckBuilder:
    """Two slides: placeholders, an inline box, a picture, a group, notes."""
    deck = DeckBuilder()
    document = container(
        ppt.RT_DOCUMENT,
        [
            atom(ppt.RT_DOCUMENT_ATOM, struct.pack("<ii", 5760, 4320) + b"\0" * 32),
            container(
                ppt.RT_SLIDE_LIST_WITH_TEXT,
                [
                    persist_atom(2, 256, 2),
                    header(0),
                    chars("Padding"),
                    header(1),
                    latin("Zero padding keeps the size\rStride moves the window"),
                    persist_atom(4, 257, 1),
                    header(6),
                    chars("Pooling"),
                ],
                instance=0,
            ),
            container(
                ppt.RT_SLIDE_LIST_WITH_TEXT,
                [persist_atom(3, 512, 1), header(2), latin("Remember the edge pixels")],
                instance=2,
            ),
        ],
    )
    deck.add(1, document)
    deck.add(
        2,
        slide_container(
            512,
            shape(anchor(400, 200, 5400, 900), outline_ref(0)),
            shape(anchor(400, 1000, 5400, 3000), outline_ref(1)),
            shape(picture_opt(1), anchor(3600, 3100, 5400, 4200)),
            shape(anchor(400, 3100, 3300, 3400), inline_text(4, "Inline note")),
            atom(ppt.RT_OE_PLACEHOLDER_ATOM, b"\0" * 8),
            shape(anchor(4800, 3900, 5600, 4200), inline_text(4, "*")),
        ),
    )
    deck.add(3, container(ppt.RT_NOTES, []))
    # A group placed at (400..1400, 3000..4000) master units, drawn in a
    # 1000x1000 space of its own; a child at (0..500, 0..500) inside it.
    deck.add(
        4,
        slide_container(
            0,
            shape(anchor(400, 200, 5400, 900), outline_ref(0)),
            container(
                ppt.ESC_SPGR_CONTAINER,
                [
                    group_head(0, 0, 1000, 1000, anchor(400, 3000, 1400, 4000)),
                    shape(child_anchor(0, 0, 500, 500), inline_text(4, "Grouped")),
                ],
            ),
        ),
    )
    deck.add_picture_png()
    deck.commit_edit(
        {1: deck.offsets[1], 2: deck.offsets[2], 3: deck.offsets[3], 4: deck.offsets[4]}
    )
    return deck


def parse(deck: DeckBuilder) -> ppt.ParsedDeck:
    return ppt.parse_streams(deck.document, deck.current_user(), deck.pictures)


# --- reading -------------------------------------------------------------------------
def test_slides_come_in_order_with_their_placeholder_text_and_size():
    parsed = parse(build_deck())
    assert (parsed.width, parsed.height) == (720.0, 540.0)
    assert [slide.title for slide in parsed.slides] == ["Padding", "Pooling"]
    first = parsed.slides[0]
    body = next(s for s in first.shapes if s.is_body and "Stride" in s.text)
    assert body.text == "Zero padding keeps the size\nStride moves the window"
    # Anchors are master units (576/inch): 400 → 50pt, 1000 → 125pt.
    assert (body.rect.x0, body.rect.y0, body.rect.x1, body.rect.y1) == (50, 125, 675, 375)


def test_inline_boxes_pictures_and_notes_are_read_and_field_markers_dropped():
    first = parse(build_deck()).slides[0]
    texts = {s.text for s in first.shapes}
    assert "Inline note" in texts
    assert "*" not in texts  # the slide-number field
    picture = next(s for s in first.shapes if s.picture)
    assert picture.picture == 1 and (picture.rect.x0, picture.rect.y0) == (450, 387.5)
    assert first.notes == "Remember the edge pixels"


def test_a_group_maps_its_children_into_the_slide():
    second = parse(build_deck()).slides[1]
    grouped = next(s for s in second.shapes if s.text == "Grouped")
    # Half the group's 1000-unit space → half its 125pt placement, from (50, 375).
    assert (grouped.rect.x0, grouped.rect.y0, grouped.rect.x1, grouped.rect.y1) == (
        50,
        375,
        112.5,
        437.5,
    )


def test_the_newest_edit_wins_after_an_incremental_save():
    deck = build_deck()
    # A later save rewrites slide 1's text; the old record stays in the file.
    replacement = slide_container(0, shape(anchor(400, 200, 5400, 900), inline_text(0, "Revised")))
    deck.add(2, replacement)
    deck.commit_edit({2: deck.offsets[2]})
    parsed = parse(deck)
    assert parsed.slides[0].title == "Revised"
    assert parsed.slides[1].title == "Pooling"  # untouched entries carry over


def test_a_password_protected_deck_says_so():
    deck = build_deck()
    with pytest.raises(UnreadablePresentationError, match="password"):
        ppt.parse_streams(deck.document, deck.current_user(encrypted=True), b"")


def test_rendering_puts_words_and_the_picture_on_pages_of_the_slide_size():
    parsed = parse(build_deck())
    rendered = ppt.render_parsed(parsed, max_slides=10)
    pdf = pymupdf.open(stream=rendered.pdf, filetype="pdf")
    assert pdf.page_count == 2 and rendered.title == "Padding"
    page = pdf[0]
    assert page.rect.width == 720 and page.rect.height == 540 + ppt._NOTES_STRIP_POINTS
    text = page.get_text()
    assert "Padding" in text and "• Stride moves the window" in text and "Inline note" in text
    assert "Speaker notes: Remember the edge pixels" in text
    assert len(page.get_images()) == 1
    assert pdf[1].rect.height == 540  # no notes, no strip
    assert "Grouped" in pdf[1].get_text()


def test_a_metafile_picture_is_reported_not_silently_dropped():
    deck = build_deck()
    deck.pictures = atom(ppt.ESC_BLIP_EMF, b"\0" * 50, instance=0x3D4)
    rendered = ppt.render_parsed(parse(deck), max_slides=10)
    assert any("metafile" in warning for warning in rendered.warnings)


def test_the_slide_limit_is_a_warning():
    rendered = ppt.render_parsed(parse(build_deck()), max_slides=1)
    assert rendered.slide_count == 1
    assert any("first 1 of 2" in warning for warning in rendered.warnings)


# --- through the container and the API ---------------------------------------------
def test_a_real_compound_file_is_recognised_and_a_damaged_one_refused(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    data = write_compound_file(build_deck().streams())
    assert ppt.looks_like_ppt(data)
    assert validate_upload("lecture.ppt", data, settings) == ppt.PPT_MEDIA_TYPE
    with pytest.raises(UnsupportedFileError, match="does not look like"):
        validate_upload("lecture.ppt", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64, settings)


def test_an_old_deck_is_indexed_and_served_as_a_pdf(client: TestClient, course):
    data = write_compound_file(build_deck().streams())
    response = client.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": ("lecture.ppt", data, "application/vnd.ms-powerpoint")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "indexed" and body["page_count"] == 2
    assert body["document"]["extraction_meta"]["extractor"] == "olefile+pymupdf"
    document_id = body["document"]["id"]
    page = client.get(f"/api/documents/{document_id}/pages/1").json()
    assert "Stride moves the window" in page["text"]
    served = client.get(f"/api/documents/{document_id}/file")
    assert served.status_code == 200 and served.content[:5] == b"%PDF-"


@pytest.mark.skipif(not os.environ.get("STUDYPILOT_TEST_PPT"), reason="set STUDYPILOT_TEST_PPT")
def test_a_lecturers_own_deck_renders_every_slide_with_text():
    path = Path(os.environ["STUDYPILOT_TEST_PPT"])
    rendered = ppt.render_ppt_to_pdf(path, max_slides=2000)
    pdf = pymupdf.open(stream=rendered.pdf, filetype="pdf")
    assert pdf.page_count == rendered.slide_count >= 1
    with_text = sum(1 for page in pdf if page.get_text().strip())
    assert with_text >= pdf.page_count * 0.8
