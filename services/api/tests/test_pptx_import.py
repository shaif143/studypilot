"""PowerPoint import.

A .pptx is rendered to a PDF by python-pptx + PyMuPDF and then goes through
the PDF pipeline unchanged. These pin what a student would notice: the slide
count, the words, the pictures and tables, the speaker notes, that the reader
is served a PDF, and that the file is recognised by its contents rather than
its name.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.ingestion.pipeline import UnsupportedFileError, validate_upload
from app.ingestion.pptx import (
    PPTX_MEDIA_TYPE,
    UnreadablePresentationError,
    looks_like_pptx,
    render_pptx_to_pdf,
)

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "sample-lecture-cnn.pptx"


@pytest.fixture
def deck_bytes() -> bytes:
    if not FIXTURE.is_file():
        pytest.skip(f"Fixture missing: {FIXTURE}. Run: python scripts/make_fixtures.py")
    return FIXTURE.read_bytes()


# --- recognition -------------------------------------------------------------
def test_a_deck_is_recognised_by_its_contents_not_its_name(deck_bytes, settings):
    assert looks_like_pptx(deck_bytes, "renamed.bin")
    assert validate_upload("renamed.bin", deck_bytes, settings) == PPTX_MEDIA_TYPE


def test_a_zip_that_is_not_a_deck_is_refused(settings):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("hello.txt", "not slides")
    assert not looks_like_pptx(buffer.getvalue(), "slides.pptx")
    with pytest.raises(UnsupportedFileError, match="does not look like"):
        validate_upload("slides.pptx", buffer.getvalue(), settings)


# --- rendering ---------------------------------------------------------------
def test_every_slide_becomes_a_page_with_its_words_in_place():
    if not FIXTURE.is_file():
        pytest.skip("fixture missing")
    deck = render_pptx_to_pdf(FIXTURE, max_slides=100)
    assert deck.slide_count == 9
    assert deck.title == "Convolutional Neural Networks"
    assert deck.warnings == []

    pdf = pymupdf.open(stream=deck.pdf, filetype="pdf")
    assert pdf.page_count == 9
    # Slide size preserved (13.333 x 7.5 in at 72 pt/in).
    assert round(pdf[1].rect.width) == 960 and round(pdf[1].rect.height) == 540

    padding = pdf[3].get_text()
    assert "Padding adds a border" in padding
    assert "Same padding keeps the output the same size" in padding
    # The picture on the padding slide survived as an image, placed on the right.
    images = pdf[3].get_images()
    assert len(images) == 1
    (x0, _, _, _) = pdf[3].get_image_rects(images[0][0])[0]
    assert x0 > 480

    # The table on slide 3 is drawn cell by cell.
    table = pdf[2].get_text()
    for cell in ("Layer", "Kernel", "conv1", "16x16x16"):
        assert cell in table

    # Bullets and the em dash are real glyphs, not "?".
    assert "•" in padding
    assert "?" not in pdf[0].get_text()


def test_speaker_notes_are_rendered_below_the_slide_not_on_it():
    if not FIXTURE.is_file():
        pytest.skip("fixture missing")
    deck = render_pptx_to_pdf(FIXTURE, max_slides=100)
    pdf = pymupdf.open(stream=deck.pdf, filetype="pdf")
    first = pdf[0]
    # The notes strip extends the page; slide content stays in the top 540 pt.
    assert first.rect.height > 540
    notes = [b for b in first.get_text("blocks") if "Speaker notes" in b[4]]
    assert notes and notes[0][1] >= 540


def test_slide_limit_is_a_warning_not_an_error():
    if not FIXTURE.is_file():
        pytest.skip("fixture missing")
    deck = render_pptx_to_pdf(FIXTURE, max_slides=2)
    assert deck.slide_count == 2
    assert any("first 2 of 9" in w for w in deck.warnings)


def test_a_broken_file_is_an_unreadable_presentation(tmp_path):
    bad = tmp_path / "bad.pptx"
    bad.write_bytes(b"PK\x03\x04 nonsense")
    with pytest.raises(UnreadablePresentationError):
        render_pptx_to_pdf(bad, max_slides=10)


# --- through the API -----------------------------------------------------------
def test_an_uploaded_deck_is_indexed_and_served_as_pdf(client: TestClient, course, deck_bytes):
    response = client.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": ("lecture7.pptx", deck_bytes, PPTX_MEDIA_TYPE)},
        data={"title": "Lecture 7 (slides)"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    document = body["document"]
    assert document["status"] == "indexed"
    assert document["media_type"] == PPTX_MEDIA_TYPE
    assert document["page_count"] == 9
    assert document["extraction_meta"]["extractor"] == "python-pptx+pymupdf"
    assert document["extraction_meta"]["text_pages"] == 9
    assert body["topic_count"] >= 5

    # The reader gets a PDF, named after the deck.
    served = client.get(f"/api/documents/{document['id']}/file")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("application/pdf")
    assert served.content[:5] == b"%PDF-"
    assert 'filename="lecture7.pdf"' in served.headers["content-disposition"]

    # Pages carry headings and bounding boxes like any PDF, so citations can highlight.
    page = client.get(f"/api/documents/{document['id']}/pages/4").json()
    assert page["heading"].lower().startswith("padding")
    assert any(el["bbox"][2] > el["bbox"][0] for el in page["elements"])

    # And the deck's words are retrievable through the tutor's retrieval path.
    pages = client.get(f"/api/documents/{document['id']}/pages").json()
    assert sum(1 for p in pages if p.get("heading")) >= 8
