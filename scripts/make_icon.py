#!/usr/bin/env python3
"""Generate the application icon source.

A plain, legible mark: a rounded indigo square with a stylised open book and a
bookmark. Drawn programmatically so the repository carries no binary asset that
cannot be regenerated, and so `tauri icon` has a 1024px source to work from.

Run with:  .venv/bin/python scripts/make_icon.py
Then:      cd apps/desktop/src-tauri && npx tauri icon ../../../assets/icon.png
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "assets" / "icon.png"
SIZE = 1024

ACCENT_TOP = (99, 91, 214)
ACCENT_BOTTOM = (67, 56, 168)
PAPER = (252, 252, 255)
INK = (46, 40, 92)


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=radius, fill=255
    )
    return mask


def vertical_gradient(
    size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]
):
    gradient = Image.new("RGB", (1, size))
    pixels = gradient.load()
    assert pixels is not None
    for y in range(size):
        ratio = y / max(1, size - 1)
        pixels[0, y] = tuple(
            round(top[channel] + (bottom[channel] - top[channel]) * ratio)
            for channel in range(3)
        )
    return gradient.resize((size, size))


def build() -> Path:
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    background = vertical_gradient(SIZE, ACCENT_TOP, ACCENT_BOTTOM).convert("RGBA")
    # macOS icons use a continuous-corner squircle; a 22% radius reads correctly.
    canvas.paste(background, (0, 0), rounded_mask(SIZE, int(SIZE * 0.225)))

    draw = ImageDraw.Draw(canvas)

    # Two facing pages, slightly lifted at the spine.
    left = [(190, 660), (500, 596), (500, 330), (190, 394)]
    right = [(834, 660), (524, 596), (524, 330), (834, 394)]
    draw.polygon(left, fill=PAPER)
    draw.polygon(right, fill=PAPER)
    draw.line([(512, 330), (512, 612)], fill=INK, width=14)

    # Text lines on each page.
    for index in range(4):
        offset = index * 52
        draw.line([(244, 432 + offset), (466, 388 + offset)], fill=INK, width=13)
        draw.line([(558, 388 + offset), (780, 432 + offset)], fill=INK, width=13)

    # Bookmark ribbon.
    draw.polygon(
        [(620, 300), (716, 300), (716, 520), (668, 464), (620, 520)],
        fill=(242, 174, 92),
    )

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(TARGET, format="PNG")
    return TARGET


if __name__ == "__main__":
    path = build()
    print(f"wrote {path} ({path.stat().st_size} bytes)")
