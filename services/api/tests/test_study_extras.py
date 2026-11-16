"""What surrounds a slide's explanation: class transcripts, the student's own
notes, explaining a whole lecture at once, and stopping work that is running.

The model is a fake throughout, so what is tested is the plumbing: that a
transcript reaches the prompt for the right slide, that a job writes every
slide once and can be stopped, that a cancelled upload or scan leaves nothing
half-added, and that one user's notes are their own.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import routes_guides
from app.ingestion import local_folders
from app.learning import guide_service, transcripts
from app.models.base import GenerationRequest, GenerationResult, ProviderInfo

REPLY = """=== SLIDE | overview | What this slide says ===
Padding keeps the output the same size.

=== SLIDE | in_class | What the lecturer added ===
In class the lecturer said padding matters most at the borders.

=== SLIDE | check | Check yourself ===
```quiz
Q: Why pad?
A: To keep the size.
```

=== VIDEOS ===
- convolution padding explained
"""


@dataclass
class FakeWriter:
    text: str = REPLY
    generative: bool = True
    requests: list[GenerationRequest] = field(default_factory=list)

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            key="fake",
            label="Fake Writer",
            sends_data_off_device=True,
            available=True,
            generative=self.generative,
            accepts_images=False,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        return GenerationResult(text=self.text, provider="fake", model="fake-1", generative=True)


@pytest.fixture
def writer(monkeypatch) -> FakeWriter:
    fake = FakeWriter()
    monkeypatch.setattr(routes_guides, "resolve_provider", lambda *a, **k: (fake, []))
    monkeypatch.setattr(guide_service, "resolve_provider", lambda *a, **k: (fake, []))
    return fake


def _wait_for_job(client: TestClient, document_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/api/documents/{document_id}/guides").json()
        if status["job"] and status["job"]["state"] not in {"queued", "running"}:
            return status
        time.sleep(0.05)
    raise AssertionError("the explain-all job did not finish")


# --- transcripts -------------------------------------------------------------------------
VTT = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
<v Lecturer>Okay so today we look at convolution.

2
00:00:04.000 --> 00:00:09.000
Okay so today we look at convolution.

3
00:00:09.000 --> 00:00:15.000
[00:09] Padding adds a border of zeros around the input.
"""


def test_a_subtitle_file_becomes_the_words_that_were_said():
    text = transcripts.clean_transcript(VTT)
    assert "-->" not in text and "WEBVTT" not in text and "<v" not in text
    # A caption repeated as it scrolls is kept once, and a leading stamp goes.
    assert text.count("Okay so today we look at convolution.") == 1
    assert "Padding adds a border of zeros around the input." in text


def test_a_file_that_is_not_a_transcript_is_refused():
    with pytest.raises(transcripts.TranscriptError):
        transcripts.read_transcript("lecture.mp4", b"\x00\x00")
    with pytest.raises(transcripts.TranscriptError):
        transcripts.read_transcript("empty.txt", b"hello")


def test_the_part_of_a_transcript_about_a_slide_is_found_by_its_words_and_its_place():
    filler = " ".join(f"general remarks about the module number {i}" for i in range(400))
    about = (
        "Now stride. The stride is how far the kernel moves each step, and a stride of two "
        "halves the output width. Remember the stride formula for the exam. "
    ) * 3
    transcript = f"{filler} {about} {filler}"
    excerpts = transcripts.excerpts_for_slide(
        transcript,
        slide_text="Stride: how far the kernel moves. Stride 2 halves the width.",
        heading="Stride",
        page_number=5,
        page_count=10,
    )
    assert excerpts and sum(len(e) for e in excerpts) <= 4200
    assert any("stride of two" in excerpt for excerpt in excerpts)
    # A short transcript is simply used whole.
    assert transcripts.excerpts_for_slide(
        "A short class. " * 10, slide_text="x", heading="y", page_number=1, page_count=3
    ) == [("A short class. " * 10).strip()]


def test_a_transcript_reaches_the_explanation_of_its_lecture(
    client: TestClient, indexed_document: dict, writer: FakeWriter
):
    document_id = indexed_document["document"]["id"]
    url = f"/api/documents/{document_id}/transcript"
    assert client.get(url).json() == {"transcript": None}

    said = "The lecturer said padding matters most at the borders of the image. " * 8
    added = client.put(url, files={"file": ("week7.vtt", said.encode(), "text/vtt")})
    assert added.status_code == 200, added.text
    transcript = added.json()["transcript"]
    assert transcript["filename"] == "week7.vtt" and transcript["word_count"] > 50
    # The status never carries the transcript's text.
    assert "text" not in transcript
    assert client.get(f"/api/documents/{document_id}/guides").json()["has_transcript"] is True

    guide = client.post(
        f"/api/documents/{document_id}/pages/4/guide", json={"allow_cloud": True}
    ).json()
    assert guide["used_transcript"] is True
    assert [card["kind"] for card in guide["cards"]] == ["overview", "in_class", "check"]
    # What the lecturer said is not "beyond the slide": it is the class itself.
    assert not any(card["beyond_slide"] for card in guide["cards"])
    prompt = writer.requests[0].user
    assert "Class transcript" in prompt and "padding matters most at the borders" in prompt

    assert client.delete(url).json() == {"transcript": None}
    again = client.post(
        f"/api/documents/{document_id}/pages/5/guide", json={"allow_cloud": True}
    ).json()
    assert again["used_transcript"] is False
    assert "Class transcript" not in writer.requests[1].user


def test_a_transcript_must_be_text_and_goes_with_its_lecture(
    client: TestClient, indexed_document: dict
):
    document_id = indexed_document["document"]["id"]
    url = f"/api/documents/{document_id}/transcript"
    refused = client.put(url, files={"file": ("class.mp4", b"\x00" * 64, "video/mp4")})
    assert refused.status_code == 400 and "text file" in refused.json()["detail"]

    client.put(url, files={"file": ("class.txt", ("words " * 60).encode(), "text/plain")})
    exported = client.get("/api/privacy/export").json()
    assert len(exported["lecture_transcripts"]) == 1

    assert client.delete(f"/api/documents/{document_id}").status_code == 204
    assert client.get("/api/privacy/export").json()["lecture_transcripts"] == []
    assert client.get("/api/documents/nope/transcript").status_code == 404


# --- the student's own notes -------------------------------------------------------------
def test_a_note_is_kept_with_its_slide_and_removed_when_emptied(
    client: TestClient, indexed_document: dict
):
    document_id = indexed_document["document"]["id"]
    url = f"/api/documents/{document_id}/pages/4/note"
    assert client.get(url).json()["text"] == ""

    saved = client.put(url, json={"text": "Same padding keeps the size. Ask about stride."})
    assert saved.status_code == 200 and saved.json()["page_number"] == 4
    assert client.get(url).json()["text"].startswith("Same padding")
    # Another slide's note is its own.
    assert client.get(f"/api/documents/{document_id}/pages/5/note").json()["text"] == ""

    listed = client.get("/api/slide-notes", params={"document_id": document_id}).json()
    assert [(n["page_number"], n["document_title"]) for n in listed] == [(4, "Lecture 7 - CNNs")]
    assert len(client.get("/api/privacy/export").json()["slide_notes"]) == 1

    assert client.put(url, json={"text": "   "}).json()["text"] == ""
    assert client.get("/api/slide-notes").json() == []
    assert client.get(f"/api/documents/{document_id}/pages/99/note").status_code == 404


# --- a whole lecture at once -------------------------------------------------------------
def test_explaining_a_lecture_writes_every_slide_once(
    client: TestClient, indexed_document: dict, writer: FakeWriter
):
    document_id = indexed_document["document"]["id"]
    # One slide is already explained; the job must not pay for it again.
    client.post(f"/api/documents/{document_id}/pages/4/guide", json={"allow_cloud": True})
    assert len(writer.requests) == 1

    started = client.post(
        f"/api/documents/{document_id}/guides/explain-all", json={"allow_cloud": True}
    )
    assert started.status_code == 200, started.text
    assert started.json()["job"]["state"] in {"queued", "running", "done"}

    status = _wait_for_job(client, document_id)
    job = status["job"]
    assert job["state"] == "done" and job["total"] == 8 and job["written"] == 8
    assert job["failed"] == 0 and job["error"] == ""
    assert status["pages"] == list(range(1, 10)) and status["page_count"] == 9
    assert len(writer.requests) == 9

    # Asked again with everything explained, there is nothing to do...
    client.post(f"/api/documents/{document_id}/guides/explain-all", json={"allow_cloud": True})
    assert _wait_for_job(client, document_id)["job"]["total"] == 0
    assert len(writer.requests) == 9
    # ...unless told to rewrite.
    client.post(
        f"/api/documents/{document_id}/guides/explain-all",
        json={"allow_cloud": True, "regenerate": True},
    )
    assert _wait_for_job(client, document_id)["job"]["written"] == 9
    assert len(writer.requests) == 18


def test_explaining_a_lecture_is_refused_without_a_model_and_stops_when_it_fails(
    client: TestClient, indexed_document: dict, writer: FakeWriter
):
    document_id = indexed_document["document"]["id"]
    url = f"/api/documents/{document_id}/guides/explain-all"

    writer.generative = False
    refused = client.post(url, json={"allow_cloud": False})
    assert refused.status_code == 409
    assert client.get(f"/api/documents/{document_id}/guides").json()["job"] is None

    # A model that answers with nothing usable: give up after a few, do not grind on.
    writer.generative = True
    writer.text = ""
    client.post(url, json={"allow_cloud": True})
    job = _wait_for_job(client, document_id)["job"]
    assert job["state"] == "failed" and job["written"] == 0
    assert job["failed"] >= guide_service.MAX_CONSECUTIVE_FAILURES
    assert job["done"] < 9
    assert client.post("/api/documents/nope/guides/explain-all", json={}).status_code == 404


def test_an_explain_all_job_can_be_stopped_and_keeps_what_it_wrote(
    context, client: TestClient, indexed_document: dict, writer: FakeWriter, monkeypatch
):
    document_id = indexed_document["document"]["id"]
    original = guide_service.draft_slide
    calls = {"count": 0}

    def slow_then_stop(**kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            guide_service.cancel(document_id)
        return original(**kwargs)

    monkeypatch.setattr(guide_service, "draft_slide", slow_then_stop)
    monkeypatch.setattr(guide_service, "PARALLEL_SLIDES", 1)
    client.post(f"/api/documents/{document_id}/guides/explain-all", json={"allow_cloud": True})
    status = _wait_for_job(client, document_id)
    assert status["job"]["state"] == "cancelled"
    # Stopped early, and everything that came back before the stop was kept.
    assert 1 <= status["job"]["written"] < 9
    assert len(status["pages"]) == status["job"]["written"]

    stopped = client.delete(f"/api/documents/{document_id}/guides/explain-all")
    assert stopped.status_code == 200 and stopped.json()["job"]["state"] == "cancelled"


# --- stopping a scan or an upload --------------------------------------------------------
FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"


def test_a_folder_scan_can_be_stopped(
    client: TestClient, course: dict, tmp_path: Path, sample_pdf_bytes: bytes, monkeypatch
):
    root = tmp_path / "Module"
    root.mkdir()
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        # Different bytes per file, so each is a document of its own.
        (root / name).write_bytes(sample_pdf_bytes + f"\n% {name}\n".encode())
    shutil.copy(FIXTURES / "sample-lecture-cnn.pptx", root / "d.pptx")

    real = local_folders._index_file
    seen: list[str] = []

    def index_then_stop(context, session, link, file, document, **kwargs):
        seen.append(file.relative)
        outcome = real(context, session, link, file, document, **kwargs)
        local_folders.cancel_scan(link.id)  # the student presses Stop during the first file
        return outcome

    monkeypatch.setattr(local_folders, "_index_file", index_then_stop)
    response = client.post(
        "/api/local-folders", json={"course_id": course["id"], "path": str(root), "wait": True}
    )
    assert response.status_code == 201, response.text
    scan = response.json()["scan"]
    assert scan["state"] == "cancelled" and scan["stopping"] is False
    assert len(seen) == 1 and scan["counts"]["cancelled"] == 3

    # The file that was read is kept; nothing is left behind as "queued".
    documents = client.get("/api/documents").json()
    assert [(d["title"], d["status"]) for d in documents] == [(Path(seen[0]).stem, "indexed")]

    # Rescanning afterwards picks up the rest.
    monkeypatch.setattr(local_folders, "_index_file", real)
    link_id = response.json()["id"]
    rescanned = client.post(f"/api/local-folders/{link_id}/scan", params={"wait": True}).json()
    assert rescanned["scan"]["state"] == "done"
    assert len(client.get("/api/documents").json()) == 4
    assert client.post("/api/local-folders/nope/cancel").status_code == 404


def test_an_upload_whose_sender_hung_up_is_not_kept(
    client: TestClient, course: dict, sample_pdf_bytes: bytes, monkeypatch
):
    from starlette.requests import Request

    async def gone(self) -> bool:
        return True

    monkeypatch.setattr(Request, "is_disconnected", gone)
    response = client.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": ("lecture.pdf", sample_pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 201  # it was indexed before the hang-up was seen
    assert client.get("/api/documents").json() == []


def test_a_cancelled_duplicate_upload_leaves_the_original_alone(
    client: TestClient, course: dict, indexed_document: dict, sample_pdf_bytes: bytes, monkeypatch
):
    from starlette.requests import Request

    async def gone(self) -> bool:
        return True

    monkeypatch.setattr(Request, "is_disconnected", gone)
    client.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": ("sample-lecture-cnn.pdf", sample_pdf_bytes, "application/pdf")},
    )
    assert [d["id"] for d in client.get("/api/documents").json()] == [
        indexed_document["document"]["id"]
    ]


def test_a_document_can_be_marked_as_a_past_paper(client: TestClient, indexed_document: dict):
    document_id = indexed_document["document"]["id"]
    changed = client.patch(f"/api/documents/{document_id}", json={"kind": "past_paper"})
    assert changed.status_code == 200 and changed.json()["kind"] == "past_paper"
    assert client.patch(f"/api/documents/{document_id}", json={"kind": "poem"}).status_code == 422
    assert client.patch("/api/documents/nope", json={"kind": "lecture"}).status_code == 404
