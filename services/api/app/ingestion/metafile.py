"""Drawing Windows metafiles (WMF and EMF) onto a PDF page.

Old PowerPoint decks keep diagrams, pasted screenshots and MathType equations
as Windows metafiles: not pictures but recorded drawing commands for the
Windows graphics layer. Nothing on a Mac draws them, and the libraries this
app already ships do not either, so a slide lost its figures.

A metafile is a list of records — "select this pen", "polyline through these
points", "write this text here" — and the set old decks actually use is small.
This plays those records back as vector drawing on the page: lines, polygons
and text stay sharp at any zoom, and a bitmap wrapped in a metafile (a very
common case: a pasted screenshot) is placed as the image it is.

It is deliberately not a complete implementation. Clipping, raster
operations, hatched brushes, arcs and bezier curves in WMF are skipped, and
text is set in the nearest of the standard PDF fonts, so an equation is
readable rather than typeset. A record that is not understood is ignored and
the rest of the drawing still appears; a metafile with nothing drawable
reports that, and the caller says so instead of leaving a blank.
"""

from __future__ import annotations

import logging
import struct
import zlib
from dataclasses import dataclass, field, replace
from typing import Any

import pymupdf

log = logging.getLogger(__name__)

Color = tuple[float, float, float]
BLACK: Color = (0.0, 0.0, 0.0)
WHITE: Color = (1.0, 1.0, 1.0)

#: Records in one metafile before giving up: a guard against a corrupt size field.
MAX_RECORDS = 200_000
MAX_POINTS = 20_000


# --- shared state ------------------------------------------------------------------------
@dataclass
class Pen:
    color: Color = BLACK
    width: float = 0.0
    visible: bool = True


@dataclass
class Brush:
    color: Color = WHITE
    visible: bool = True


@dataclass
class Font:
    height: float = 12.0
    bold: bool = False
    italic: bool = False
    face: str = ""
    escapement: float = 0.0


@dataclass
class State:
    pen: Pen = field(default_factory=Pen)
    brush: Brush = field(default_factory=Brush)
    font: Font = field(default_factory=Font)
    text_color: Color = BLACK
    text_align: int = 0
    even_odd: bool = True
    position: tuple[float, float] = (0.0, 0.0)
    window_org: tuple[float, float] = (0.0, 0.0)
    window_ext: tuple[float, float] | None = None


def _color(value: int) -> Color:
    return ((value & 0xFF) / 255, ((value >> 8) & 0xFF) / 255, ((value >> 16) & 0xFF) / 255)


def _font_name(font: Font) -> str:
    face = font.face.lower()
    if "symbol" in face:
        return "symb"
    if "courier" in face or "consol" in face or "mono" in face:
        family = "co"
        return family + (
            "bi"
            if font.bold and font.italic
            else "bo"
            if font.bold
            else "it"
            if font.italic
            else "ur"
        )
    serif = any(
        name in face
        for name in (
            "times",
            "roman",
            "georgia",
            "cambria",
            "garamond",
            "serif",
            "mt extra",
            "euclid",
        )
    )
    if serif and "sans" not in face:
        return "ti" + (
            "bi"
            if font.bold and font.italic
            else "bo"
            if font.bold
            else "it"
            if font.italic
            else "ro"
        )
    return "he" + (
        "bi" if font.bold and font.italic else "bo" if font.bold else "it" if font.italic else "lv"
    )


def dib_to_bmp(dib: bytes) -> bytes | None:
    """A device-independent bitmap is a BMP file without its 14-byte file header."""
    if len(dib) < 40:
        return None
    header_size = struct.unpack_from("<I", dib, 0)[0]
    if header_size == 12:  # the old OS/2 core header
        bit_count = struct.unpack_from("<H", dib, 10)[0]
        palette = (1 << bit_count) * 3 if bit_count <= 8 else 0
    elif 40 <= header_size <= 124:
        bit_count = struct.unpack_from("<H", dib, 14)[0]
        compression, _image_size, _xppm, _yppm, used = struct.unpack_from("<IIiiI", dib, 16)
        colors = used or ((1 << bit_count) if bit_count <= 8 else 0)
        palette = colors * 4
        if compression == 3 and header_size == 40:  # BI_BITFIELDS keeps three masks after it
            palette += 12
    else:
        return None
    offset = 14 + header_size + palette
    if offset > 14 + len(dib):
        return None
    return b"BM" + struct.pack("<IHHI", 14 + len(dib), 0, 0, offset) + dib


class Canvas:
    """Where the records are played: one rectangle of one PDF page."""

    def __init__(self, page: pymupdf.Page, rect: pymupdf.Rect) -> None:
        self.page = page
        self.rect = rect
        self.shape = page.new_shape()
        self.drawn = 0
        #: Text is placed after the shapes are committed, so it is never under a fill.
        self._texts: list[tuple[pymupdf.Point, str, str, float, Color, float]] = []
        self._images: list[tuple[pymupdf.Rect, bytes]] = []

    def path(
        self, points: list[pymupdf.Point], state: State, *, closed: bool, scale: float
    ) -> None:
        if len(points) < 2:
            return
        self.shape.draw_polyline(points)
        self._finish(state, closed=closed, scale=scale)

    def paths(self, polygons: list[list[pymupdf.Point]], state: State, *, scale: float) -> None:
        drew = False
        for points in polygons:
            if len(points) >= 2:
                self.shape.draw_polyline([*points, points[0]])
                drew = True
        if drew:
            self._finish(state, closed=True, scale=scale)

    def rectangle(self, rect: pymupdf.Rect, state: State, *, scale: float) -> None:
        self.shape.draw_rect(rect)
        self._finish(state, closed=True, scale=scale)

    def ellipse(self, rect: pymupdf.Rect, state: State, *, scale: float) -> None:
        if rect.is_empty:
            return
        self.shape.draw_oval(rect)
        self._finish(state, closed=True, scale=scale)

    def _finish(self, state: State, *, closed: bool, scale: float) -> None:
        stroke = state.pen.color if state.pen.visible else None
        fill = state.brush.color if (closed and state.brush.visible) else None
        if stroke is None and fill is None:
            # Nothing would be painted. Drop the path: finishing it with no
            # colour at all would stroke it in the default black.
            self.shape.draw_cont = ""
            return
        self.shape.finish(
            color=stroke,
            fill=fill,
            width=max(0.35, state.pen.width * scale) if stroke else 0,
            closePath=closed,
            even_odd=state.even_odd,
            lineJoin=1,
            lineCap=1,
        )
        self.drawn += 1

    def text(self, point: pymupdf.Point, text: str, state: State, *, scale: float) -> None:
        text = text.replace("\x00", "").rstrip("\r\n")
        if not text.strip():
            return
        font = state.font
        size = abs(font.height) * scale
        if font.height > 0:  # a cell height, which includes the line gap
            size *= 0.86
        if not 2.0 <= size <= 400:
            size = min(max(size, 2.0), 400.0)
        name = _font_name(font)
        try:
            width = pymupdf.get_text_length(text, fontname=name, fontsize=size)
        except Exception:
            width = len(text) * size * 0.5
        align = state.text_align
        x, y = point.x, point.y
        horizontal = align & 6
        if horizontal == 6:  # TA_CENTER
            x -= width / 2
        elif horizontal == 2:  # TA_RIGHT
            x -= width
        vertical = align & 24
        if vertical == 0:  # TA_TOP: the point is the top of the cell
            y += size * 0.88
        elif vertical == 8:  # TA_BOTTOM
            y -= size * 0.2
        self._texts.append(
            (pymupdf.Point(x, y), text, name, size, state.text_color, font.escapement)
        )

    def image(self, rect: pymupdf.Rect, dib: bytes) -> None:
        bmp = dib_to_bmp(dib)
        rect = pymupdf.Rect(rect).normalize()
        if bmp is None or rect.is_empty:
            return
        self._images.append((rect, bmp))

    def commit(self) -> int:
        for rect, bmp in self._images:
            try:
                self.page.insert_image(rect & self.page.rect, stream=bmp, keep_proportion=False)
                self.drawn += 1
            except Exception as exc:
                log.debug("metafile_bitmap_skipped", extra={"reason": str(exc)})
        try:
            self.shape.commit()
        except Exception as exc:
            log.debug("metafile_shapes_skipped", extra={"reason": str(exc)})
        for point, text, name, size, color, escapement in self._texts:
            try:
                rotate = round(escapement / 90.0) * 90 % 360 if escapement else 0
                self.page.insert_text(
                    point, text, fontname=name, fontsize=size, color=color, rotate=rotate
                )
                self.drawn += 1
            except Exception as exc:
                log.debug("metafile_text_skipped", extra={"reason": str(exc)})
        return self.drawn


# --- WMF ---------------------------------------------------------------------------------
_PLACEABLE_KEY = 0x9AC6CDD7


def _wmf_decode(raw: bytes, symbol: bool) -> str:
    # Symbol's codes are its own encoding, which is also what the PDF Symbol font uses.
    return raw.decode("latin-1" if symbol else "cp1252", errors="replace")


def play_wmf(
    data: bytes, canvas: Canvas, bounds: tuple[float, float, float, float] | None = None
) -> None:
    """Play a WMF. ``bounds`` is the frame to fall back on when the file sets no window."""
    position = 0
    if len(data) >= 22 and struct.unpack_from("<I", data, 0)[0] == _PLACEABLE_KEY:
        left, top, right, bottom = struct.unpack_from("<hhhh", data, 6)
        bounds = bounds or (left, top, right, bottom)
        position = 22
    if len(data) < position + 18:
        return
    header_words = struct.unpack_from("<H", data, position + 2)[0]
    position += header_words * 2 if header_words >= 9 else 18

    state = State()
    if bounds:
        state.window_org = (bounds[0], bounds[1])
        state.window_ext = (bounds[2] - bounds[0], bounds[3] - bounds[1])
    stack: list[State] = []
    objects: list[Any] = []
    rect = canvas.rect

    def add(obj: Any) -> None:
        for index, slot in enumerate(objects):
            if slot is None:
                objects[index] = obj
                return
        objects.append(obj)

    def scale_of() -> tuple[float, float]:
        ext = state.window_ext
        if not ext or not ext[0] or not ext[1]:
            return 1.0, 1.0
        return rect.width / ext[0], rect.height / ext[1]

    def to_page(x: float, y: float) -> pymupdf.Point:
        sx, sy = scale_of()
        return pymupdf.Point(
            rect.x0 + (x - state.window_org[0]) * sx, rect.y0 + (y - state.window_org[1]) * sy
        )

    def stroke_scale() -> float:
        sx, sy = scale_of()
        return (abs(sx) + abs(sy)) / 2

    for _ in range(MAX_RECORDS):
        if position + 6 > len(data):
            break
        size_words, function = struct.unpack_from("<IH", data, position)
        if size_words < 3:
            break
        params = data[position + 6 : position + size_words * 2]
        position += size_words * 2
        count = len(params) // 2

        def words(start: int = 0, number: int | None = None, _p: bytes = params, _c: int = count):
            number = _c - start if number is None else number
            return struct.unpack_from(f"<{number}h", _p, start * 2)

        try:
            if function == 0x0000:  # EOF
                break
            elif function == 0x020B and count >= 2:  # SetWindowOrg
                y, x = words(0, 2)
                state.window_org = (x, y)
            elif function == 0x020C and count >= 2:  # SetWindowExt
                y, x = words(0, 2)
                if x and y:
                    state.window_ext = (x, y)
            elif function == 0x001E:  # SaveDC
                stack.append(replace(state))
            elif function == 0x0127:  # RestoreDC
                if stack:
                    state = stack.pop()
            elif function == 0x0209 and len(params) >= 4:  # SetTextColor
                state.text_color = _color(struct.unpack_from("<I", params, 0)[0])
            elif function == 0x012E and count >= 1:  # SetTextAlign
                state.text_align = struct.unpack_from("<H", params, 0)[0]
            elif function == 0x0106 and count >= 1:  # SetPolyFillMode
                state.even_odd = words(0, 1)[0] != 2
            elif function == 0x02FA and len(params) >= 10:  # CreatePenIndirect
                style, width, _unused, color = struct.unpack_from("<HhhI", params, 0)
                add(Pen(color=_color(color), width=abs(width), visible=(style & 0xF) != 5))
            elif function == 0x02FC and len(params) >= 6:  # CreateBrushIndirect
                style, color = struct.unpack_from("<HI", params, 0)
                add(Brush(color=_color(color), visible=style != 1))
            elif function == 0x02FB and len(params) >= 18:  # CreateFontIndirect
                height, _width, escapement, _orientation, weight = struct.unpack_from(
                    "<hhhhh", params, 0
                )
                italic = params[10]
                face = params[18:50].split(b"\x00", 1)[0].decode("latin-1", errors="replace")
                add(
                    Font(
                        height=height or 12,
                        bold=weight >= 600,
                        italic=bool(italic),
                        face=face,
                        escapement=escapement / 10.0,
                    )
                )
            elif function in (0x00F7, 0x06FF, 0x0142, 0x01F9, 0x02FD):
                # A palette, a region, a pattern brush: it takes a slot in the
                # object table even though nothing here draws with it.
                add(Brush(color=(0.75, 0.75, 0.75)) if function in (0x0142, 0x01F9) else object())
            elif function == 0x012D and count >= 1:  # SelectObject
                index = struct.unpack_from("<H", params, 0)[0]
                obj = objects[index] if index < len(objects) else None
                if isinstance(obj, Pen):
                    state.pen = obj
                elif isinstance(obj, Brush):
                    state.brush = obj
                elif isinstance(obj, Font):
                    state.font = obj
            elif function == 0x01F0 and count >= 1:  # DeleteObject
                index = struct.unpack_from("<H", params, 0)[0]
                if index < len(objects):
                    objects[index] = None
            elif function == 0x0214 and count >= 2:  # MoveTo
                y, x = words(0, 2)
                state.position = (x, y)
            elif function == 0x0213 and count >= 2:  # LineTo
                y, x = words(0, 2)
                canvas.path(
                    [to_page(*state.position), to_page(x, y)],
                    state,
                    closed=False,
                    scale=stroke_scale(),
                )
                state.position = (x, y)
            elif function in (0x0325, 0x0324) and count >= 1:  # Polyline, Polygon
                number = min(struct.unpack_from("<H", params, 0)[0], (count - 1) // 2, MAX_POINTS)
                flat = words(1, number * 2)
                points = [to_page(flat[i], flat[i + 1]) for i in range(0, len(flat), 2)]
                canvas.path(points, state, closed=function == 0x0324, scale=stroke_scale())
            elif function == 0x0538 and count >= 1:  # PolyPolygon
                polygons_count = struct.unpack_from("<H", params, 0)[0]
                sizes = struct.unpack_from(f"<{polygons_count}H", params, 2)
                cursor = 1 + polygons_count
                polygons = []
                for declared in sizes:
                    size = min(declared, MAX_POINTS)
                    if cursor + size * 2 > count:
                        break
                    flat = words(cursor, size * 2)
                    polygons.append([to_page(flat[i], flat[i + 1]) for i in range(0, len(flat), 2)])
                    cursor += size * 2
                canvas.paths(polygons, state, scale=stroke_scale())
            elif function in (0x041B, 0x0418) and count >= 4:  # Rectangle, Ellipse
                bottom, right, top, left = words(0, 4)
                box = pymupdf.Rect(to_page(left, top), to_page(right, bottom)).normalize()
                if function == 0x041B:
                    canvas.rectangle(box, state, scale=stroke_scale())
                else:
                    canvas.ellipse(box, state, scale=stroke_scale())
            elif function == 0x061C and count >= 6:  # RoundRect, drawn square-cornered
                _h, _w, bottom, right, top, left = words(0, 6)
                box = pymupdf.Rect(to_page(left, top), to_page(right, bottom)).normalize()
                canvas.rectangle(box, state, scale=stroke_scale())
            elif function == 0x0521 and count >= 1:  # TextOut
                length = struct.unpack_from("<H", params, 0)[0]
                raw = params[2 : 2 + length]
                offset = 2 + length + (length & 1)
                y, x = struct.unpack_from("<hh", params, offset)
                symbol = "symbol" in state.font.face.lower()
                canvas.text(
                    to_page(x, y), _wmf_decode(raw, symbol), state, scale=abs(scale_of()[1])
                )
            elif function == 0x0A32 and count >= 4:  # ExtTextOut
                y, x, length, options = struct.unpack_from("<hhHH", params, 0)
                offset = 8 + (8 if options & 0x0006 else 0)  # an opaque or clipping rectangle
                raw = params[offset : offset + length]
                symbol = "symbol" in state.font.face.lower()
                canvas.text(
                    to_page(x, y), _wmf_decode(raw, symbol), state, scale=abs(scale_of()[1])
                )
            elif function == 0x0B41 and len(params) >= 20 + 40:  # DibStretchBlt
                _rop, _sh, _sw, _sy, _sx, dh, dw, dy, dx = struct.unpack_from(
                    "<Ihhhhhhhh", params, 0
                )
                canvas.image(pymupdf.Rect(to_page(dx, dy), to_page(dx + dw, dy + dh)), params[20:])
            elif function == 0x0F43 and len(params) >= 22 + 40:  # StretchDIBits
                _rop, _use, _sh, _sw, _sy, _sx, dh, dw, dy, dx = struct.unpack_from(
                    "<IHhhhhhhhh", params, 0
                )
                canvas.image(pymupdf.Rect(to_page(dx, dy), to_page(dx + dw, dy + dh)), params[22:])
            elif function == 0x0940 and len(params) >= 16 + 40:  # DibBitBlt
                _rop, _sy, _sx, height, width, dy, dx = struct.unpack_from("<Ihhhhhh", params, 0)
                canvas.image(
                    pymupdf.Rect(to_page(dx, dy), to_page(dx + width, dy + height)), params[16:]
                )
        except (struct.error, IndexError, ValueError):
            # One malformed record; the rest of the drawing is still worth having.
            continue


# --- EMF ---------------------------------------------------------------------------------
def play_emf(data: bytes, canvas: Canvas) -> None:
    """Play the common subset of EMF: bitmaps, 16-bit polygons and polylines, lines, text."""
    if len(data) < 88 or struct.unpack_from("<I", data, 0)[0] != 1:
        return
    left, top, right, bottom = struct.unpack_from("<iiii", data, 8)
    width, height = max(1, right - left + 1), max(1, bottom - top + 1)
    rect = canvas.rect
    state = State()
    stack: list[State] = []
    objects: dict[int, Any] = {}
    window = {"org": (0.0, 0.0), "ext": (1.0, 1.0), "vorg": (0.0, 0.0), "vext": (1.0, 1.0)}
    scaled = False  # MM_ISOTROPIC / MM_ANISOTROPIC: only then do the extents apply

    def to_page(x: float, y: float) -> pymupdf.Point:
        if scaled and window["ext"][0] and window["ext"][1]:
            x = (x - window["org"][0]) * window["vext"][0] / window["ext"][0] + window["vorg"][0]
            y = (y - window["org"][1]) * window["vext"][1] / window["ext"][1] + window["vorg"][1]
        return pymupdf.Point(
            rect.x0 + (x - left) / width * rect.width, rect.y0 + (y - top) / height * rect.height
        )

    def stroke_scale() -> float:
        factor = (rect.width / width + rect.height / height) / 2
        if scaled and window["ext"][0]:
            factor *= abs(window["vext"][0] / window["ext"][0])
        return factor

    position = 0
    for _ in range(MAX_RECORDS):
        if position + 8 > len(data):
            break
        kind, size = struct.unpack_from("<II", data, position)
        if size < 8 or position + size > len(data):
            break
        record = data[position : position + size]
        position += size
        try:
            if kind == 14:  # EOF
                break
            elif kind == 17:  # SetMapMode
                scaled = struct.unpack_from("<I", record, 8)[0] in (7, 8)
            elif kind == 9:
                window["ext"] = struct.unpack_from("<ii", record, 8)
            elif kind == 10:
                window["org"] = struct.unpack_from("<ii", record, 8)
            elif kind == 11:
                window["vext"] = struct.unpack_from("<ii", record, 8)
            elif kind == 12:
                window["vorg"] = struct.unpack_from("<ii", record, 8)
            elif kind == 33:  # SaveDC
                stack.append(replace(state))
            elif kind == 34:  # RestoreDC
                if stack:
                    state = stack.pop()
            elif kind == 24:  # SetTextColor
                state.text_color = _color(struct.unpack_from("<I", record, 8)[0])
            elif kind == 22:  # SetTextAlign
                state.text_align = struct.unpack_from("<I", record, 8)[0]
            elif kind == 19:  # SetPolyFillMode
                state.even_odd = struct.unpack_from("<I", record, 8)[0] != 2
            elif kind == 38:  # CreatePen
                index, style, pen_width, _y, color = struct.unpack_from("<IIiiI", record, 8)
                objects[index] = Pen(
                    color=_color(color), width=abs(pen_width), visible=(style & 0xF) != 5
                )
            elif kind == 95:  # ExtCreatePen
                index = struct.unpack_from("<I", record, 8)[0]
                style, pen_width, _brush_style, color = struct.unpack_from("<IIII", record, 28)
                objects[index] = Pen(
                    color=_color(color), width=abs(pen_width), visible=(style & 0xF) != 5
                )
            elif kind == 39:  # CreateBrushIndirect
                index, style, color = struct.unpack_from("<III", record, 8)
                objects[index] = Brush(color=_color(color), visible=style != 1)
            elif kind == 82:  # ExtCreateFontIndirectW
                index, font_height = struct.unpack_from("<Ii", record, 8)
                escapement, _orientation, weight = struct.unpack_from("<iii", record, 20)
                italic = record[32]
                face = record[40:104].decode("utf-16-le", errors="replace").split("\x00", 1)[0]
                objects[index] = Font(
                    height=font_height or 12,
                    bold=weight >= 600,
                    italic=bool(italic),
                    face=face,
                    escapement=escapement / 10.0,
                )
            elif kind == 37:  # SelectObject
                index = struct.unpack_from("<I", record, 8)[0]
                if index & 0x80000000:  # a stock object
                    stock = index & 0x7FFFFFFF
                    if stock in (0, 1, 2, 3, 4):
                        shade = (1.0, 0.75, 0.5, 0.25, 0.0)[stock]
                        state.brush = Brush(color=(shade, shade, shade))
                    elif stock == 5:
                        state.brush = Brush(visible=False)
                    elif stock in (6, 7):
                        state.pen = Pen(color=WHITE if stock == 6 else BLACK)
                    elif stock == 8:
                        state.pen = Pen(visible=False)
                else:
                    obj = objects.get(index)
                    if isinstance(obj, Pen):
                        state.pen = obj
                    elif isinstance(obj, Brush):
                        state.brush = obj
                    elif isinstance(obj, Font):
                        state.font = obj
            elif kind == 40:  # DeleteObject
                objects.pop(struct.unpack_from("<I", record, 8)[0], None)
            elif kind == 27:  # MoveToEx
                state.position = struct.unpack_from("<ii", record, 8)
            elif kind == 54:  # LineTo
                x, y = struct.unpack_from("<ii", record, 8)
                canvas.path(
                    [to_page(*state.position), to_page(x, y)],
                    state,
                    closed=False,
                    scale=stroke_scale(),
                )
                state.position = (x, y)
            elif kind in (3, 4, 86, 87):  # Polygon, Polyline and their 16-bit forms
                number = min(struct.unpack_from("<I", record, 24)[0], MAX_POINTS)
                code, step = ("h", 2) if kind in (86, 87) else ("i", 4)
                flat = struct.unpack_from(f"<{number * 2}{code}", record, 28)
                del step
                points = [to_page(flat[i], flat[i + 1]) for i in range(0, len(flat), 2)]
                canvas.path(points, state, closed=kind in (3, 86), scale=stroke_scale())
            elif kind in (8, 7, 91, 90):  # PolyPolygon, PolyPolyline and their 16-bit forms
                polys, total = struct.unpack_from("<II", record, 24)
                sizes = struct.unpack_from(f"<{polys}I", record, 32)
                code, step = ("h", 2) if kind in (91, 90) else ("i", 4)
                cursor = 32 + polys * 4
                polygons = []
                for declared in sizes:
                    number = min(declared, MAX_POINTS)
                    flat = struct.unpack_from(f"<{number * 2}{code}", record, cursor)
                    cursor += number * 2 * step
                    polygons.append([to_page(flat[i], flat[i + 1]) for i in range(0, len(flat), 2)])
                del total
                if kind in (8, 91):
                    canvas.paths(polygons, state, scale=stroke_scale())
                else:
                    for points in polygons:
                        canvas.path(points, state, closed=False, scale=stroke_scale())
            elif kind in (43, 42):  # Rectangle, Ellipse
                box_left, box_top, box_right, box_bottom = struct.unpack_from("<iiii", record, 8)
                box = pymupdf.Rect(
                    to_page(box_left, box_top), to_page(box_right, box_bottom)
                ).normalize()
                if kind == 43:
                    canvas.rectangle(box, state, scale=stroke_scale())
                else:
                    canvas.ellipse(box, state, scale=stroke_scale())
            elif kind == 84:  # ExtTextOutW
                x, y, chars, offset = struct.unpack_from("<iiII", record, 36)
                raw = record[offset : offset + chars * 2]
                canvas.text(
                    to_page(x, y),
                    raw.decode("utf-16-le", errors="replace"),
                    state,
                    scale=rect.height / height,
                )
            elif kind == 81:  # StretchDIBits
                x, y, _sx, _sy, _sw, _sh, off_bmi, cb_bmi, off_bits, cb_bits = struct.unpack_from(
                    "<iiiiiiIIII", record, 24
                )
                dest_w, dest_h = struct.unpack_from("<ii", record, 72)
                dib = record[off_bmi : off_bmi + cb_bmi] + record[off_bits : off_bits + cb_bits]
                canvas.image(pymupdf.Rect(to_page(x, y), to_page(x + dest_w, y + dest_h)), dib)
            elif kind in (76, 77):  # BitBlt, StretchBlt carrying a bitmap
                x, y, dest_w, dest_h = struct.unpack_from("<iiii", record, 24)
                off_bmi, cb_bmi, off_bits, cb_bits = struct.unpack_from("<IIII", record, 84)
                if cb_bmi and cb_bits:
                    dib = record[off_bmi : off_bmi + cb_bmi] + record[off_bits : off_bits + cb_bits]
                    canvas.image(pymupdf.Rect(to_page(x, y), to_page(x + dest_w, y + dest_h)), dib)
        except (struct.error, IndexError, ValueError):
            continue


# --- entry points ------------------------------------------------------------------------
def draw_metafile(
    page: pymupdf.Page,
    rect: pymupdf.Rect,
    data: bytes,
    *,
    bounds: tuple[float, float, float, float] | None = None,
) -> bool:
    """Draw a WMF or EMF into ``rect``. True if anything was drawn."""
    rect = pymupdf.Rect(rect).normalize()
    if rect.is_empty or len(data) < 18:
        return False
    canvas = Canvas(page, rect)
    try:
        if len(data) >= 44 and data[:4] == b"\x01\x00\x00\x00" and data[40:44] == b" EMF":
            play_emf(data, canvas)
        else:
            play_wmf(data, canvas, bounds)
        return canvas.commit() > 0
    except Exception:
        log.exception("metafile_failed")
        return False


def unwrap_office_blip(
    body: bytes, *, two_uids: bool
) -> tuple[bytes, tuple[float, float, float, float]] | None:
    """The metafile inside an Office picture record, and the frame Office recorded for it.

    Office stores a metafile behind one or two 16-byte ids and a 34-byte header
    giving its bounds, and usually deflates it.
    """
    offset = 16 * (2 if two_uids else 1)
    if len(body) < offset + 34:
        return None
    _size, left, top, right, bottom, _w, _h, _saved, compression, _filter = struct.unpack_from(
        "<IiiiiiiIBB", body, offset
    )
    data = body[offset + 34 :]
    if compression == 0:
        try:
            data = zlib.decompress(data)
        except zlib.error:
            return None
    return data, (left, top, right, bottom)
