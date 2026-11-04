"""PDF extraction, topic detection and chunking."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ingestion.chunking import build_chunks
from app.ingestion.pdf import UnreadableDocumentError, extract_pdf
from app.ingestion.pipeline import UnsupportedFileError, validate_upload
from app.ingestion.topics import detect_topics, normalise_heading, slugify


def test_extracts_pages_text_and_headings(sample_pdf_bytes: bytes, tmp_path: Path):
    source = tmp_path / "lecture.pdf"
    source.write_bytes(sample_pdf_bytes)

    document = extract_pdf(source, max_pages=50, preview_dir=tmp_path / "previews")

    assert document.page_count == 9
    assert document.text_pages == 9
    # The real heading from page 4 must be recovered, not guessed.
    headings = [page.heading for page in document.pages]
    assert "Padding" in headings
    assert "Stride" in headings
    padding_page = next(page for page in document.pages if page.heading == "Padding")
    assert "Padding is necessary" in padding_page.text


def test_preserves_bounding_boxes_for_citations(sample_pdf_bytes: bytes, tmp_path: Path):
    source = tmp_path / "lecture.pdf"
    source.write_bytes(sample_pdf_bytes)
    document = extract_pdf(source, max_pages=50, preview_dir=None)

    page = document.pages[3]
    assert page.elements, "expected extracted elements"
    for element in page.elements:
        x0, y0, x1, y1 = element.bbox
        assert x1 > x0 and y1 > y0
        assert 0 <= x0 <= page.width + 1
        assert 0 <= y0 <= page.height + 1


def test_renders_page_previews(sample_pdf_bytes: bytes, tmp_path: Path):
    source = tmp_path / "lecture.pdf"
    source.write_bytes(sample_pdf_bytes)
    previews = tmp_path / "previews"
    document = extract_pdf(source, max_pages=50, preview_dir=previews)

    assert document.pages[0].preview_filename
    assert (previews / document.pages[0].preview_filename).is_file()


def test_detects_equations(sample_pdf_bytes: bytes, tmp_path: Path):
    source = tmp_path / "lecture.pdf"
    source.write_bytes(sample_pdf_bytes)
    document = extract_pdf(source, max_pages=50, preview_dir=None)
    assert any(page.has_equations for page in document.pages)


def test_rejects_non_pdf(tmp_path: Path):
    bad = tmp_path / "notes.pdf"
    bad.write_bytes(b"this is not a pdf at all")
    with pytest.raises(UnreadableDocumentError):
        extract_pdf(bad, max_pages=10)


def test_page_limit_is_reported_not_silent(sample_pdf_bytes: bytes, tmp_path: Path):
    source = tmp_path / "lecture.pdf"
    source.write_bytes(sample_pdf_bytes)
    document = extract_pdf(source, max_pages=3, preview_dir=None)
    assert document.page_count == 3
    assert any("page limit" in warning for warning in document.warnings)


def test_validate_upload_checks_magic_bytes(settings):
    assert validate_upload("a.pdf", b"%PDF-1.7\nrest", settings) == "application/pdf"
    with pytest.raises(UnsupportedFileError):
        validate_upload("a.pdf", b"PK\x03\x04 not a pdf", settings)
    with pytest.raises(UnsupportedFileError, match="PowerPoint"):
        validate_upload("deck.pptx", b"PK\x03\x04", settings)
    with pytest.raises(UnsupportedFileError, match="empty"):
        validate_upload("a.pdf", b"", settings)


def test_upload_size_limit(settings):
    settings.max_upload_bytes = 10
    with pytest.raises(UnsupportedFileError, match="larger than"):
        validate_upload("a.pdf", b"%PDF-" + b"x" * 100, settings)


def test_topics_group_consecutive_pages(sample_pdf_bytes: bytes, tmp_path: Path):
    source = tmp_path / "lecture.pdf"
    source.write_bytes(sample_pdf_bytes)
    document = extract_pdf(source, max_pages=50, preview_dir=None)

    topics = detect_topics(document.pages, fallback_title="CNNs")
    slugs = {topic.slug for topic in topics}
    assert "padding" in slugs
    assert "stride" in slugs
    for topic in topics:
        assert topic.first_page <= topic.last_page


def test_heading_normalisation():
    assert normalise_heading("Slide 4: Padding") == "Padding"
    assert normalise_heading("3. Stride") == "Stride"
    assert slugify("Backpropagation through convolution") == "backpropagation-through-convolution"


def test_chunking_produces_parents_and_children(sample_pdf_bytes: bytes, tmp_path: Path):
    source = tmp_path / "lecture.pdf"
    source.write_bytes(sample_pdf_bytes)
    document = extract_pdf(source, max_pages=50, preview_dir=None)

    chunks = build_chunks(document.pages, child_chars=400, child_overlap=50)
    parents = [chunk for chunk in chunks if chunk.level == "parent"]
    children = [chunk for chunk in chunks if chunk.level == "child"]

    assert len(parents) == document.text_pages
    assert children
    # Every child points at a parent and keeps its page number.
    for child in children:
        assert child.parent_ordinal is not None
        assert child.page_number >= 1
