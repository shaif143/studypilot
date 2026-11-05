"""Archive uploads: a .zip or .rar of lecture files, member by member.

A zip is built in the test with the real fixtures inside, plus the things a
lecturer's archive carries along: Finder metadata, a text file, an image, a
nested archive, and a path that tries to climb out. Each readable member
becomes a document; each other member is reported with a reason; nothing is
written under an attacker's path. RAR needs `bsdtar` and a real file:
set STUDYPILOT_TEST_RAR to run that one.
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ingestion import archives

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"


def build_zip(pdf: bytes) -> bytes:
    deck = FIXTURES / "sample-lecture-cnn.pptx"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Week 1/lecture.pdf", pdf)
        archive.writestr("Week 1/deck.pptx", deck.read_bytes())
        archive.writestr("Week 1/readme.txt", "not a lecture")
        archive.writestr("Week 1/figure.png", b"\x89PNG not really")
        archive.writestr("Week 1/more.zip", b"PK\x03\x04 nested")
        archive.writestr("__MACOSX/Week 1/._lecture.pdf", b"finder junk")
        archive.writestr("Week 1/.DS_Store", b"finder junk")
        archive.writestr("../escape.pdf", pdf)
    return buffer.getvalue()


def test_a_zip_yields_its_readable_members_and_names_the_rest(sample_pdf_bytes: bytes):
    unpacked = archives.unpack(
        build_zip(sample_pdf_bytes), "zip", max_member_bytes=10_000_000, max_total_bytes=50_000_000
    )
    assert [m.path for m in unpacked.members] == ["Week 1/lecture.pdf", "Week 1/deck.pptx"]
    assert unpacked.members[0].data == sample_pdf_bytes
    reasons = {s.path: s.reason for s in unpacked.skipped}
    assert "model" in reasons["Week 1/figure.png"]
    assert "inside an archive" in reasons["Week 1/more.zip"]
    assert reasons["Week 1/readme.txt"] == "Not a supported type."
    assert reasons["../escape.pdf"] == "Unsafe path inside the archive."
    assert not any("DS_Store" in path or "__MACOSX" in path for path in reasons)


def test_members_over_the_limits_are_skipped_not_read(sample_pdf_bytes: bytes):
    unpacked = archives.unpack(
        build_zip(sample_pdf_bytes), "zip", max_member_bytes=1000, max_total_bytes=50_000_000
    )
    assert unpacked.members == []
    assert all(
        "limit" in s.reason
        for s in unpacked.skipped
        if s.path.startswith("Week 1/") and s.path.endswith((".pdf", ".pptx"))
    )


def test_the_kind_is_read_from_the_bytes_not_the_name():
    assert archives.archive_kind(b"PK\x03\x04...", "x.rar") == "zip"
    assert archives.archive_kind(b"Rar!\x1a\x07\x00...", "x.zip") == "rar"
    assert archives.archive_kind(b"%PDF-1.4", "x.zip") == ""


def test_a_broken_zip_is_an_error_with_a_message():
    with pytest.raises(archives.UnreadableArchiveError, match="could not be opened"):
        archives.unpack(b"PK\x03\x04 nope", "zip", max_member_bytes=10, max_total_bytes=10)


# --- through the API ----------------------------------------------------------------
def test_uploading_a_zip_indexes_each_member_as_its_own_document(
    client: TestClient, course, sample_pdf_bytes: bytes
):
    response = client.post(
        f"/api/courses/{course['id']}/documents/archive",
        files={"file": ("week1.zip", build_zip(sample_pdf_bytes), "application/zip")},
        data={"kind": "lecture"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["archive"] == "week1.zip"
    by_path = {m["path"]: m for m in body["members"]}
    assert by_path["Week 1/lecture.pdf"]["status"] == "indexed"
    assert by_path["Week 1/lecture.pdf"]["result"]["document"]["title"] == "lecture"
    assert by_path["Week 1/deck.pptx"]["status"] == "indexed"
    assert by_path["Week 1/readme.txt"]["status"] == "skipped"
    assert by_path["../escape.pdf"]["status"] == "skipped"
    titles = {d["title"] for d in client.get("/api/documents").json()}
    assert titles == {"lecture", "deck"}


def test_something_that_is_not_an_archive_is_refused(client: TestClient, course):
    response = client.post(
        f"/api/courses/{course['id']}/documents/archive",
        files={"file": ("week1.zip", b"%PDF-1.4 not a zip", "application/zip")},
    )
    assert response.status_code == 400
    assert "not a .zip or .rar" in response.json()["detail"]


@pytest.mark.skipif(not os.environ.get("STUDYPILOT_TEST_RAR"), reason="set STUDYPILOT_TEST_RAR")
def test_a_real_rar_is_listed_and_unpacked():
    data = Path(os.environ["STUDYPILOT_TEST_RAR"]).read_bytes()
    assert archives.archive_kind(data) == "rar"
    unpacked = archives.unpack(
        data, "rar", max_member_bytes=80_000_000, max_total_bytes=320_000_000
    )
    assert unpacked.members or unpacked.skipped
    for member in unpacked.members:
        assert ".." not in member.path and member.data
