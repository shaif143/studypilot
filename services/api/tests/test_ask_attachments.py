"""A question that brings something with it: files, photos, a GitHub issue, a command.

The model is a stand-in that records what it was sent, so these check what
reaches it: a picture as a picture, a PDF and a text file as text, and
nothing kept afterwards but the names.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents import attachments
from app.agents import service as agent_service
from app.integrations import github_issues
from app.models.base import GenerationRequest, GenerationResult, ProviderInfo

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=="
)


@dataclass
class Recorder:
    """A generative model that answers anything and keeps what it was asked."""

    images: bool = True
    generative: bool = True
    requests: list[GenerationRequest] = field(default_factory=list)

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            key="fake",
            label="Fake",
            sends_data_off_device=True,
            available=True,
            generative=self.generative,
            accepts_images=self.images,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        return GenerationResult(
            text="It says MANGO-42.", provider="fake", model="f", generative=self.generative
        )


@pytest.fixture
def model(monkeypatch) -> Recorder:
    fake = Recorder()
    monkeypatch.setattr(agent_service, "resolve_provider", lambda *a, **k: (fake, []))
    return fake


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def pdf(text: str) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), text, fontsize=12)
    return doc.tobytes()


def ask(client: TestClient, document: dict, question: str, files: list[tuple[str, bytes]]):
    return client.post(
        "/api/tutor/ask",
        json={
            "question": question,
            "document_id": document["document"]["id"],
            "page_number": 2,
            "allow_cloud": True,
            "attachments": [{"filename": name, "data": b64(data)} for name, data in files],
        },
    )


def test_a_photo_goes_as_a_picture_and_a_pdf_and_a_text_file_as_text(
    client: TestClient, indexed_document: dict, model: Recorder
):
    response = ask(
        client,
        indexed_document,
        "What does my working say?",
        [
            ("working.png", PNG),
            ("paper.pdf", pdf("The lab password is MANGO-42 and nothing else.")),
            ("notes.md", b"# Notes\nStride halves the output.\n"),
        ],
    )
    assert response.status_code == 200, response.text
    request = model.requests[0]
    assert [(a.media_type, a.data, a.label) for a in request.attachments] == [
        ("image/png", PNG, "working.png")
    ]
    assert "The student attached the following" in request.user
    assert '<attached_file name="paper.pdf">' in request.user
    assert "The lab password is MANGO-42" in request.user
    assert '<attached_file name="notes.md">' in request.user and "Stride halves" in request.user
    assert "Attached as pictures: working.png." in request.user
    # The question itself is still what the course is searched for.
    assert request.question == "What does my working say?"

    # Only the names are kept, beside the question.
    session_id = response.json()["session_id"]
    messages = client.get(f"/api/sessions/{session_id}/messages").json()
    mine = next(message for message in messages if message["role"] == "user")
    assert mine["meta"]["attachments"] == ["working.png", "paper.pdf", "notes.md"]
    assert "MANGO-42" not in str(mine)


def test_an_attachment_is_reason_enough_to_answer_when_the_course_has_nothing(
    client: TestClient, course: dict, model: Recorder
):
    """With nothing indexed the tutor declines, unless the student brought the material."""
    body = {"question": "zzqx vortices?", "course_id": course["id"], "allow_cloud": True}
    assert client.post("/api/tutor/ask", json=body).json()["abstained"] is True
    assert model.requests == []

    body["attachments"] = [{"filename": "a.txt", "data": b64(b"zzqx vortices are made up.\n")}]
    answered = client.post("/api/tutor/ask", json=body).json()
    assert answered["abstained"] is False and answered["answer"] == "It says MANGO-42."
    assert "zzqx vortices are made up." in model.requests[0].user


def test_a_model_that_cannot_see_is_not_shown_the_photo_and_says_so(
    client: TestClient, indexed_document: dict, model: Recorder
):
    model.images = False
    response = ask(client, indexed_document, "What is this?", [("board.png", PNG)])
    assert model.requests[0].attachments == []
    assert any("cannot read pictures" in warning for warning in response.json()["warnings"])


def test_without_a_model_the_attachments_are_said_not_to_have_been_read(
    client: TestClient, indexed_document: dict, model: Recorder
):
    model.generative = False
    response = ask(client, indexed_document, "What is convolution?", [("a.txt", b"hello\n")])
    assert any("were not read" in warning for warning in response.json()["warnings"])
    assert "hello" not in model.requests[0].user and model.requests[0].attachments == []


def test_what_cannot_be_read_is_refused_by_name(
    client: TestClient, indexed_document: dict, model: Recorder
):
    deck = ask(client, indexed_document, "Read this", [("deck.pptx", b"PK\x03\x04" + b"\x00" * 64)])
    assert deck.status_code == 400 and "deck.pptx cannot be read" in deck.json()["detail"]
    broken = client.post(
        "/api/tutor/ask",
        json={
            "question": "x",
            "document_id": indexed_document["document"]["id"],
            "attachments": [{"filename": "a.png", "data": "not base64!"}],
        },
    )
    assert broken.status_code == 400 and "did not arrive whole" in broken.json()["detail"]
    assert model.requests == []


def test_limits_and_scans_are_reported(settings):
    with pytest.raises(ValueError, match="up to 10 files"):
        attachments.decode([{"filename": f"{n}.txt", "data": b64(b"x")} for n in range(11)])
    big = b64(b"0" * (attachments.MAX_TOTAL_BYTES // 2 + 1))
    with pytest.raises(ValueError, match="larger than 20 MB"):
        attachments.decode([{"filename": "a", "data": big}, {"filename": "b", "data": big}])

    import pymupdf

    blank = pymupdf.open()
    blank.new_page()
    prepared = attachments.prepare([("scan.pdf", blank.tobytes())], settings, reads_images=True)
    assert prepared.text == "" and "probably a scan" in prepared.warnings[0]

    long = attachments.prepare(
        [("long.txt", ("word " * 30_000).encode())], settings, reads_images=True
    )
    assert len(long.text) < attachments.MAX_TEXT_CHARS + 200
    assert "Only the first part of long.txt" in long.warnings[0]


# --- GitHub ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "reference",
    [
        "https://github.com/octo/widgets/issues/12",
        "github.com/octo/widgets/pull/12#issuecomment-5",
        "https://www.github.com/octo/widgets/issues/12?utm=x",
        "octo/widgets#12",
        "  octo/widgets#12  ",
    ],
)
def test_an_issue_is_named_by_its_link_or_its_short_form(reference: str):
    assert github_issues.parse_reference(reference) == ("octo", "widgets", 12)


@pytest.mark.parametrize(
    "reference",
    [
        "https://example.com/octo/widgets/issues/12",
        "octo/widgets",
        "12",
        "http://github.com@evil/x",
    ],
)
def test_anything_else_is_not_an_issue(reference: str):
    with pytest.raises(github_issues.GitHubImportError):
        github_issues.parse_reference(reference)


@pytest.fixture
def github(monkeypatch) -> list[str]:
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert request.url.host == "api.github.com"
        path = request.url.path
        if path == "/repos/octo/widgets/issues/12":
            return httpx.Response(
                200,
                json={
                    "title": "Kernel size off by one",
                    "state": "open",
                    "user": {"login": "ada"},
                    "labels": [{"name": "bug"}],
                    "body": "The output is 4 wide, not 5.",
                    "comments": 1,
                    "html_url": "https://github.com/octo/widgets/issues/12",
                },
            )
        if path == "/repos/octo/widgets/issues/12/comments":
            return httpx.Response(
                200,
                json=[
                    {
                        "user": {"login": "grace"},
                        "created_at": "2026-03-01T10:00:00Z",
                        "body": "Padding was left out.",
                    }
                ],
            )
        return httpx.Response(404, json={"message": "Not Found"})

    monkeypatch.setattr(github_issues, "TRANSPORT", httpx.MockTransport(answer))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    return seen


def test_an_issue_comes_in_with_its_comments_as_text(client: TestClient, github: list[str]):
    response = client.post("/api/tutor/github-issue", json={"reference": "octo/widgets#12"})
    assert response.status_code == 200, response.text
    issue = response.json()
    assert issue["filename"] == "widgets-12.md" and issue["comment_count"] == 1
    assert issue["title"] == "Kernel size off by one"
    assert "# Issue octo/widgets#12: Kernel size off by one" in issue["text"]
    assert "opened by ada" in issue["text"] and "labels: bug" in issue["text"]
    assert "## Comment by grace on 2026-03-01\nPadding was left out." in issue["text"]
    assert all(url.startswith("https://api.github.com/repos/octo/widgets/") for url in github)


def test_a_missing_or_private_issue_says_so(client: TestClient, github: list[str]):
    missing = client.post("/api/tutor/github-issue", json={"reference": "octo/widgets#99"})
    assert missing.status_code == 400 and "private" in missing.json()["detail"]
    nonsense = client.post("/api/tutor/github-issue", json={"reference": "not an issue"})
    assert nonsense.status_code == 400 and "owner/repository#123" in nonsense.json()["detail"]


# --- commands -------------------------------------------------------------------------
def test_a_plugin_command_typed_in_a_question_is_carried_out(
    client: TestClient, indexed_document: dict, model: Recorder
):
    import io
    import json
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(".claude-plugin/plugin.json", json.dumps({"name": "Study Kit"}))
        archive.writestr("commands/quiz-me.md", "Ask three questions about: $ARGUMENTS\n")
    added = client.post("/api/assistant/plugins", files={"file": ("kit.zip", buffer.getvalue())})
    assert added.status_code == 201, added.text

    ask(client, indexed_document, "/quiz-me padding", [])
    sent = model.requests[0].user
    assert "ran the command /study-kit:quiz-me" in sent
    assert "Ask three questions about: padding" in sent
