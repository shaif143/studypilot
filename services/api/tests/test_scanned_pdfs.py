"""Scanned PDFs: no text layer, read page by page with the vision model.

A page image is made here (a PDF holding only a picture), so the extractor
finds no text. With a consenting vision provider every page is transcribed
and the document is indexed like any other; without one it stays partial and
says what would read it. The same upload failing twice ends as one document.
"""

from __future__ import annotations

import pymupdf
from fastapi.testclient import TestClient
from test_media_briefs import FakeVision, wav


def scanned_pdf(pages: int = 2) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 120), False)
    pix.set_rect(pix.irect, (255, 255, 255))
    png = pix.tobytes("png")
    pdf = pymupdf.open()
    for _ in range(pages):
        page = pdf.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=png)
    data = pdf.tobytes()
    pdf.close()
    return data


def test_without_a_vision_model_a_scan_is_partial_and_says_what_would_read_it(
    client: TestClient, course
):
    response = client.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": ("scan.pdf", scanned_pdf(), "application/pdf")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "partial" and body["chunk_count"] == 0
    assert any("reads the pages as images" in w for w in body["warnings"])


def test_with_a_consenting_vision_model_every_page_is_read(client: TestClient, course, monkeypatch):
    from app.api import routes_documents

    vision = FakeVision(text="Question 3\nFind the LPC coefficients of the signal $x[n]$.")
    monkeypatch.setattr(routes_documents, "resolve_provider", lambda *a, **k: (vision, []))
    response = client.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": ("scan.pdf", scanned_pdf(3), "application/pdf")},
        data={"allow_cloud": "true"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "indexed" and body["page_count"] == 3
    assert body["chunk_count"] >= 3
    assert any("read from their images by Fake Vision" in w for w in body["warnings"])
    assert body["document"]["extraction_meta"]["extractor"].endswith("+vision:fake")
    assert len(vision.requests) == 3  # one page image per request
    assert vision.requests[0].attachments[0].media_type == "image/png"
    page = client.get(f"/api/documents/{body['document']['id']}/pages/2").json()
    assert "LPC coefficients" in page["text"] and page["heading"] == "Question 3"
    # The transcript is searchable like any other page.
    answer = client.post(
        "/api/tutor/ask",
        json={"question": "What are the LPC coefficients?", "course_id": course["id"]},
    )
    assert answer.status_code == 200
    assert any(c["document_id"] == body["document"]["id"] for c in answer.json()["citations"])


def test_a_born_digital_pdf_never_goes_to_the_model(
    client: TestClient, course, sample_pdf_bytes, monkeypatch
):
    from app.api import routes_documents

    vision = FakeVision()
    monkeypatch.setattr(routes_documents, "resolve_provider", lambda *a, **k: (vision, []))
    response = client.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": ("lecture.pdf", sample_pdf_bytes, "application/pdf")},
        data={"allow_cloud": "true"},
    )
    assert response.status_code == 201 and response.json()["status"] == "indexed"
    assert vision.requests == []


def test_the_same_failed_upload_is_retried_in_place(client: TestClient, course, monkeypatch):
    from app.ingestion import media

    monkeypatch.setattr(media, "local_transcription_available", lambda: False)
    monkeypatch.setattr(
        media,
        "_transcribe_locally",
        lambda *a, **k: (_ for _ in ()).throw(media._LocalTranscriptionUnavailable("absent")),
    )
    for _ in range(2):
        response = client.post(
            f"/api/courses/{course['id']}/documents",
            files={"file": ("clip.wav", wav(), "audio/wav")},
        )
        assert response.status_code == 201 and response.json()["status"] == "failed"
    documents = client.get("/api/documents").json()
    assert len(documents) == 1 and documents[0]["status"] == "failed"
