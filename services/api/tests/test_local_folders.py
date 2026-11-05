"""Folders on this machine, indexed in place.

A real folder is made under tmp_path with the fixtures inside. Linking it
indexes every readable file where it is — no blob, no previews — and a
rescan follows the folder: a new file is indexed, an edited one re-read, a
deleted one removed, an unchanged one left alone. A linked file is served
to the reader from disk, and only while it still lies in a linked folder.
Paths outside a linked folder, the home folder and the disk root are refused.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ingestion import local_folders

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"


@pytest.fixture
def course_folder(tmp_path: Path, sample_pdf_bytes: bytes) -> Path:
    root = tmp_path / "EEEM030"
    (root / "L1").mkdir(parents=True)
    (root / "L2").mkdir()
    (root / "L1" / "lecture.pdf").write_bytes(sample_pdf_bytes)
    shutil.copy(FIXTURES / "sample-lecture-cnn.pptx", root / "L2" / "deck.pptx")
    (root / "L2" / "notes.txt").write_text("not indexed")
    (root / ".DS_Store").write_bytes(b"junk")
    (root / "L1" / "figure.png").write_bytes(b"\x89PNG not really")
    return root


def _link(client: TestClient, course: dict, path: Path, **extra) -> dict:
    response = client.post(
        "/api/local-folders",
        json={"course_id": course["id"], "path": str(path), "wait": True, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_linking_a_folder_indexes_its_files_in_place(
    client: TestClient, course, course_folder: Path, settings
):
    link = _link(client, course, course_folder)
    assert link["path"] == str(course_folder.resolve())
    assert link["scan"]["state"] == "done"
    # Two documents, the image skipped (no consent for a model), the text file ignored.
    assert link["scan"]["counts"] == {"indexed": 2, "skipped": 1}
    assert link["last_summary"] == link["scan"]["counts"]

    documents = {d["title"]: d for d in client.get("/api/documents").json()}
    assert set(documents) == {"lecture", "deck"}
    lecture = documents["lecture"]
    assert lecture["source"] == "local_folder"
    assert lecture["source_ref"] == str(course_folder.resolve() / "L1" / "lecture.pdf")
    assert lecture["extraction_meta"]["folder_path"] == "L1/lecture.pdf"
    # Nothing copied: no blob for either file, no previews for the PDF.
    assert not any(p.is_file() for p in settings.blob_dir.rglob("*"))
    assert not list(settings.preview_dir.glob(f"{lecture['id']}/*"))
    # ...but the deck keeps its rendered PDF, which is what the reader shows.
    assert documents["deck"]["extraction_meta"]["rendered_pdf"]
    # Indexed like any other document.
    page = client.get(f"/api/documents/{lecture['id']}/pages/4").json()
    assert "Padding" in page["text"]
    served = client.get(f"/api/documents/{lecture['id']}/file")
    assert served.status_code == 200 and served.content[:5] == b"%PDF-"


def test_a_rescan_follows_the_folder(
    client: TestClient, course, course_folder: Path, sample_pdf_bytes: bytes
):
    link = _link(client, course, course_folder)
    before = {d["title"]: d["id"] for d in client.get("/api/documents").json()}

    # Nothing changed: nothing re-read.
    unchanged = client.post(f"/api/local-folders/{link['id']}/scan?wait=true").json()
    assert unchanged["scan"]["counts"] == {"unchanged": 2, "skipped": 1}

    # A new file, an edited file, a deleted file.
    (course_folder / "L2" / "past-paper.pdf").write_bytes(sample_pdf_bytes)
    lecture = course_folder / "L1" / "lecture.pdf"
    lecture.write_bytes(sample_pdf_bytes + b"\n%edited")
    future = time.time() + 5
    os.utime(lecture, (future, future))
    (course_folder / "L2" / "deck.pptx").unlink()

    changed = client.post(f"/api/local-folders/{link['id']}/scan?wait=true").json()
    assert changed["scan"]["counts"] == {"indexed": 2, "removed": 1, "skipped": 1}
    after = {d["title"]: d["id"] for d in client.get("/api/documents").json()}
    assert set(after) == {"lecture", "past-paper"}
    assert after["lecture"] != before["lecture"]  # re-read as a new document
    assert client.get(f"/api/documents/{before['deck']}").status_code == 404


def test_a_scan_runs_in_the_background_and_reports_progress(
    client: TestClient, course, course_folder: Path
):
    response = client.post(
        "/api/local-folders", json={"course_id": course["id"], "path": str(course_folder)}
    )
    assert response.status_code == 201
    link_id = response.json()["id"]
    for _ in range(200):
        listing = client.get("/api/local-folders").json()
        link = next(row for row in listing["links"] if row["id"] == link_id)
        if link["scan"] and link["scan"]["state"] in {"done", "failed"}:
            break
        time.sleep(0.05)
    assert link["scan"]["state"] == "done", link
    assert link["scan"]["done"] == link["scan"]["total"] == 3
    assert len(client.get("/api/documents").json()) == 2


def test_unlinking_drops_the_index_and_the_reader_refuses_a_moved_file(
    client: TestClient, course, course_folder: Path
):
    link = _link(client, course, course_folder)
    lecture = next(d for d in client.get("/api/documents").json() if d["title"] == "lecture")
    # Moved out of the folder: the index stays until a rescan, but the file is not read.
    (course_folder / "L1" / "lecture.pdf").rename(course_folder.parent / "elsewhere.pdf")
    served = client.get(f"/api/documents/{lecture['id']}/file")
    assert served.status_code == 409 and "Rescan" in served.json()["detail"]

    after = client.delete(f"/api/local-folders/{link['id']}").json()
    assert after["links"] == []
    assert client.get("/api/documents").json() == []


@pytest.mark.parametrize(
    "path",
    ["relative/folder", "/", str(Path.home()), "/definitely/not/here"],
)
def test_folders_that_must_not_be_linked_are_refused(client: TestClient, course, path: str):
    response = client.post(
        "/api/local-folders", json={"course_id": course["id"], "path": path, "wait": True}
    )
    assert response.status_code == 400, response.text


def test_a_file_is_refused_when_it_has_no_link(client: TestClient, course, course_folder: Path):
    link = _link(client, course, course_folder)
    lecture = next(d for d in client.get("/api/documents").json() if d["title"] == "lecture")
    # Pretend the row points outside any linked folder.
    from app.storage.models import Document

    with client.app.state.context.database.session() as session:
        row = session.get(Document, lecture["id"])
        row.source_ref = str(course_folder.parent / "outside.pdf")
        session.commit()
    (course_folder.parent / "outside.pdf").write_bytes(b"%PDF-1.4 secret")
    served = client.get(f"/api/documents/{lecture['id']}/file")
    assert served.status_code == 409
    assert b"secret" not in served.content
    assert link["id"]


def test_images_are_read_only_with_consent_and_a_model(
    client: TestClient, course, course_folder: Path, monkeypatch
):
    from test_media_briefs import FakeVision

    from app.api import routes_documents

    vision = FakeVision(text="A block diagram of a vocoder.")
    monkeypatch.setattr(routes_documents, "resolve_provider", lambda *a, **k: (vision, []))
    import pymupdf

    png = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False).tobytes("png")
    (course_folder / "L1" / "figure.png").write_bytes(png)

    link = _link(client, course, course_folder, read_media=True)
    assert link["scan"]["counts"] == {"indexed": 3}
    titles = {d["title"] for d in client.get("/api/documents").json()}
    assert "figure" in titles and len(vision.requests) == 1


def test_hosted_mode_has_no_folders(hosted: TestClient):
    assert hosted.get("/api/local-folders").status_code in {200, 401}


def test_hidden_and_unreadable_entries_are_left_out(course_folder: Path):
    files = local_folders.list_files(course_folder, recursive=True)
    names = [f.relative for f in files]
    assert ".DS_Store" not in names and "L2/notes.txt" not in names
    assert names == ["L1/figure.png", "L1/lecture.pdf", "L2/deck.pptx"]
    assert [f.relative for f in local_folders.list_files(course_folder, recursive=False)] == []
