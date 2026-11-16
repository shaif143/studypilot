"""Windows metafiles drawn onto a slide.

The metafiles are built here record by record, so each test says exactly what
drawing commands it contains and checks what reached the page: a line as a
vector path, text as text, a wrapped bitmap as an image.
"""

from __future__ import annotations

import struct
import zlib

import pymupdf

from app.ingestion import ppt
from app.ingestion.metafile import dib_to_bmp, draw_metafile, unwrap_office_blip


def _record(function: int, params: bytes = b"") -> bytes:
    if len(params) % 2:
        params += b"\x00"
    return struct.pack("<IH", 3 + len(params) // 2, function) + params


def _wmf(*records: bytes) -> bytes:
    body = b"".join(records) + _record(0x0000)
    header = struct.pack("<HHHIHIH", 1, 9, 0x0300, (18 + len(body)) // 2, 3, 64, 0)
    return header + body


def _dib(width: int = 2, height: int = 2) -> bytes:
    header = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, 0, 2835, 2835, 0, 0)
    row = b"\xff\x00\x00" * width
    row += b"\x00" * (-len(row) % 4)
    return header + row * height


def _page() -> tuple[pymupdf.Document, pymupdf.Page]:
    doc = pymupdf.open()
    return doc, doc.new_page(width=400, height=300)


def test_lines_polygons_and_text_are_played_back_as_vectors():
    wmf = _wmf(
        _record(0x020B, struct.pack("<hh", 0, 0)),  # SetWindowOrg
        _record(0x020C, struct.pack("<hh", 1000, 2000)),  # SetWindowExt: 2000 wide, 1000 tall
        _record(0x02FA, struct.pack("<HhhI", 0, 10, 0, 0x000000FF)),  # a red pen
        _record(0x012D, struct.pack("<H", 0)),  # SelectObject(pen)
        _record(0x0325, struct.pack("<Hhhhh", 2, 0, 0, 2000, 1000)),  # Polyline corner to corner
        _record(
            0x02FB, struct.pack("<hhhhh", -200, 0, 0, 0, 400) + bytes(8) + b"Times New Roman\x00"
        ),
        _record(0x012D, struct.pack("<H", 1)),  # SelectObject(font)
        _record(0x0A32, struct.pack("<hhHH", 500, 100, 5, 0) + b"x(t)="),  # ExtTextOut
    )
    doc, page = _page()
    rect = pymupdf.Rect(100, 50, 300, 150)
    assert draw_metafile(page, rect, wmf) is True

    drawings = page.get_drawings()
    assert len(drawings) == 1
    line = drawings[0]
    # Red, and mapped from the metafile's own units onto the rectangle it was given.
    assert line["color"] == (1.0, 0.0, 0.0)
    start, end = line["items"][0][1], line["items"][0][2]
    assert (round(start.x), round(start.y)) == (100, 50)
    assert (round(end.x), round(end.y)) == (300, 150)
    assert "x(t)=" in page.get_text()
    doc.close()


def test_a_bitmap_wrapped_in_a_metafile_is_placed_as_the_image_it_is():
    params = struct.pack("<Ihhhhhhhh", 0x00CC0020, 2, 2, 0, 0, 100, 100, 0, 0) + _dib()
    wmf = _wmf(
        _record(0x020C, struct.pack("<hh", 100, 100)),
        _record(0x0B41, params),  # DibStretchBlt
    )
    doc, page = _page()
    assert draw_metafile(page, pymupdf.Rect(10, 10, 110, 110), wmf) is True
    assert len(page.get_images()) == 1 and page.get_drawings() == []
    assert dib_to_bmp(_dib())[:2] == b"BM"
    assert dib_to_bmp(b"\x00" * 10) is None
    doc.close()


def test_an_object_that_is_not_drawn_still_takes_its_slot():
    """A palette created before a pen must not shift which object "1" is."""
    wmf = _wmf(
        _record(0x020C, struct.pack("<hh", 100, 100)),
        _record(0x00F7, struct.pack("<HH", 0x0300, 0)),  # CreatePalette -> slot 0
        _record(0x02FA, struct.pack("<HhhI", 0, 1, 0, 0x00FF0000)),  # a blue pen -> slot 1
        _record(0x012D, struct.pack("<H", 1)),
        _record(0x0214, struct.pack("<hh", 0, 0)),  # MoveTo
        _record(0x0213, struct.pack("<hh", 100, 100)),  # LineTo
    )
    doc, page = _page()
    assert draw_metafile(page, pymupdf.Rect(0, 0, 100, 100), wmf) is True
    assert page.get_drawings()[0]["color"] == (0.0, 0.0, 1.0)
    doc.close()


def test_what_cannot_be_drawn_says_so_instead_of_failing():
    doc, page = _page()
    rect = pymupdf.Rect(0, 0, 100, 100)
    assert draw_metafile(page, rect, b"") is False
    assert draw_metafile(page, rect, b"not a metafile at all, just some bytes") is False
    assert draw_metafile(page, rect, _wmf()) is False  # valid, but draws nothing
    # A record whose size runs past the end of the file is skipped, not followed.
    truncated = _wmf(_record(0x020C, struct.pack("<hh", 100, 100)))[:-4] + b"\xff\xff\xff\x7f"
    assert draw_metafile(page, rect, truncated) is False
    assert page.get_drawings() == [] and page.get_images() == []
    doc.close()


def test_a_bitmap_in_an_emf_is_placed():
    dib = _dib()
    header = bytearray(88)
    struct.pack_into("<II", header, 0, 1, 88)
    struct.pack_into("<iiii", header, 8, 0, 0, 99, 99)  # bounds, in device units
    header[40:44] = b" EMF"
    stretch = bytearray(80)
    struct.pack_into("<II", stretch, 0, 81, 80 + len(dib))
    struct.pack_into("<iiiiiiIIII", stretch, 24, 0, 0, 0, 0, 2, 2, 80, 40, 120, len(dib) - 40)
    struct.pack_into("<ii", stretch, 72, 100, 100)
    emf = bytes(header) + bytes(stretch) + dib + struct.pack("<IIIII", 14, 20, 0, 0, 20)
    doc, page = _page()
    assert draw_metafile(page, pymupdf.Rect(20, 20, 220, 220), emf) is True
    assert len(page.get_images()) == 1
    doc.close()


def test_a_metafile_in_an_old_deck_is_unwrapped_and_drawn():
    wmf = _wmf(
        _record(0x02FA, struct.pack("<HhhI", 0, 5, 0, 0)),
        _record(0x012D, struct.pack("<H", 0)),
        _record(0x0325, struct.pack("<Hhhhh", 2, 0, 0, 500, 500)),
    )
    blip_header = struct.pack(
        "<IiiiiiiIBB", len(wmf), 0, 0, 500, 500, 100, 100, len(zlib.compress(wmf)), 0, 0xFE
    )
    body = bytes(16) + blip_header + zlib.compress(wmf)
    data, bounds = unwrap_office_blip(body, two_uids=False)
    assert data == wmf and bounds == (0, 0, 500, 500)
    assert unwrap_office_blip(bytes(16) + blip_header + b"not deflate", two_uids=False) is None

    # No window record in the file: the frame Office stored is what places it.
    picture = ppt.Picture(data=data, kind="metafile", bounds=bounds)
    doc, page = _page()
    assert draw_metafile(page, pymupdf.Rect(0, 0, 200, 200), picture.data, bounds=bounds)
    end = page.get_drawings()[0]["items"][0][2]
    assert (round(end.x), round(end.y)) == (200, 200)
    doc.close()
