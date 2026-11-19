"""Teaching slides written for a page.

The model is a fake that returns the delimited format, so what is tested is
everything around it: that a reply becomes slides, that a model's slides are
kept and the second read costs nothing, that the slide's own text is what
comes back without a model and is *not* kept, that the slide is shown to a
model that can see, that video links are built here rather than taken from
the model, and that one user cannot read another's.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from app.api import routes_guides
from app.learning import slide_guide
from app.models.base import (
    GenerationRequest,
    GenerationResult,
    ProviderInfo,
    ProviderUnavailableError,
)

REPLY = """=== SLIDE | overview | What padding is for ===
Padding adds a border of zeros so the output keeps its size.

=== SLIDE | equation | Output size ===
$$o = \\frac{n + 2p - k}{s} + 1$$

Here $n$ is the input width, $p$ the padding, $k$ the kernel and $s$ the stride.

```mermaid
flowchart LR
  Input --> Pad --> Convolve
```

=== SLIDE | example | A worked case ===
With $n=5$, $k=3$, $p=1$, $s=1$ the output is 5 wide. See [this page](https://example.com/x).

```plot
{"title": "Width", "x": "p", "y": "w", "series": [{"name": "k=3", "points": [[0, 3], [1, 5]]}]}
```

=== VIDEOS ===
- convolution padding and stride explained
- https://youtube.com/watch?v=invented
- 2. same vs valid padding CNN
"""


@dataclass
class FakeWriter:
    """A generative provider that records what it was sent."""

    text: str = REPLY
    images: bool = True
    fail: bool = False
    requests: list[GenerationRequest] = field(default_factory=list)

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            key="fake",
            label="Fake Writer",
            sends_data_off_device=True,
            available=True,
            accepts_images=self.images,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        if self.fail:
            raise ProviderUnavailableError("the model is down")
        return GenerationResult(text=self.text, provider="fake", model="fake-1", generative=True)


@pytest.fixture
def writer(monkeypatch) -> FakeWriter:
    fake = FakeWriter()
    monkeypatch.setattr(routes_guides, "resolve_provider", lambda *a, **k: (fake, []))
    return fake


def _guide_url(document: dict, page: int = 4) -> str:
    return f"/api/documents/{document['document']['id']}/pages/{page}/guide"


# --- the format -------------------------------------------------------------
def test_a_reply_becomes_teaching_slides_and_search_phrases():
    cards, queries = slide_guide.parse_guide(REPLY)
    assert [(card.kind, card.title) for card in cards] == [
        ("overview", "What padding is for"),
        ("equation", "Output size"),
        ("example", "A worked case"),
    ]
    # LaTeX, the diagram and the plot survive untouched.
    assert "\\frac{n + 2p - k}{s}" in cards[1].markdown
    assert "```mermaid" in cards[1].markdown and "```plot" in cards[2].markdown
    # A link the model wrote keeps its words and loses its address.
    assert "this page" in cards[2].markdown and "example.com" not in cards[2].markdown
    # An address is not a search phrase; list numbering is stripped.
    assert queries == ["convolution padding and stride explained", "same vs valid padding CNN"]


def test_a_reply_without_the_format_is_still_one_slide():
    cards, queries = slide_guide.parse_guide("Padding keeps the output the same size.")
    assert [(card.kind, card.title) for card in cards] == [("explanation", "Explanation")]
    assert queries == []
    assert slide_guide.parse_guide("   ") == ([], [])


def test_a_table_in_a_teaching_slide_is_kept_as_written():
    table = "| Padding | Width |\n| :-- | --: |\n| valid | 3 |\n| same | 5 |"
    cards, _ = slide_guide.parse_guide(
        f"=== SLIDE | explanation | Valid against same ===\nCompare:\n\n{table}\n"
    )

    assert len(cards) == 1
    assert cards[0].title == "Valid against same"
    assert table in cards[0].markdown


def test_the_prompts_allow_a_table():
    from app.agents import prompts

    assert "Markdown table" in slide_guide.SYSTEM
    assert "cannot draw" not in slide_guide.SYSTEM
    assert "cannot draw" not in prompts._GROUNDING_RULES


def test_an_unknown_kind_is_an_explanation_and_slides_are_capped():
    many = "\n".join(f"=== SLIDE | wisdom | Part {i} ===\nBody {i}." for i in range(9))
    cards, _ = slide_guide.parse_guide(many)
    assert len(cards) == slide_guide.MAX_CARDS
    assert {card.kind for card in cards} == {"explanation"}


def test_video_links_are_searches_built_here():
    links = slide_guide.video_links(["same vs valid padding CNN"])
    assert links == [
        {
            "title": "same vs valid padding CNN",
            "url": "https://www.youtube.com/results?search_query=same+vs+valid+padding+CNN",
        }
    ]


# --- the endpoint -----------------------------------------------------------
def test_a_slide_is_explained_once_and_kept(
    client: TestClient, indexed_document: dict, writer: FakeWriter
):
    url = _guide_url(indexed_document)
    assert client.get(url).json() == {"guide": None}
    assert len(writer.requests) == 0  # reading never calls a model

    written = client.post(url, json={"allow_cloud": True})
    assert written.status_code == 200, written.text
    guide = written.json()
    assert guide["generated_by"] == "llm:fake" and guide["model"] == "fake-1"
    assert guide["saved"] is True and guide["page_number"] == 4
    assert [card["kind"] for card in guide["cards"]] == ["overview", "equation", "example"]
    # Only the model's own additions are marked as going beyond the slide.
    assert [card["beyond_slide"] for card in guide["cards"]] == [False, False, True]
    urls = [link["url"] for link in guide["videos"]]
    assert all(url.startswith("https://www.youtube.com/results?") for url in urls)
    assert len(guide["videos"]) == 2

    # The prompt carried this slide, its neighbours and the course.
    request = writer.requests[0]
    assert "Slide 4 of" in request.user and "Padding" in request.user
    assert "Course: Deep Learning" in request.user and "Lecture 7 - CNNs" in request.user
    # A model that can see is shown the slide itself.
    assert [a.media_type for a in request.attachments] == ["image/png"]
    assert request.attachments[0].data[:8] == b"\x89PNG\r\n\x1a\n"
    assert "attached as an image" in request.user

    # Opening it again, and asking again, reads what was kept.
    assert client.get(url).json()["guide"]["cards"] == guide["cards"]
    assert client.post(url, json={"allow_cloud": True}).json()["cards"] == guide["cards"]
    assert len(writer.requests) == 1

    listed = client.get(f"/api/documents/{indexed_document['document']['id']}/guides").json()
    assert listed["pages"] == [4]


def test_regenerating_replaces_the_kept_guide(
    client: TestClient, indexed_document: dict, writer: FakeWriter
):
    url = _guide_url(indexed_document)
    client.post(url, json={"allow_cloud": True})
    writer.text = "=== SLIDE | overview | Second attempt ===\nA different explanation."
    again = client.post(url, json={"allow_cloud": True, "regenerate": True, "level": "simple"})
    assert again.status_code == 200
    assert [card["title"] for card in again.json()["cards"]] == ["Second attempt"]
    assert again.json()["level"] == "simple"
    assert "first time" in writer.requests[-1].user  # the simple-level guidance
    assert client.get(url).json()["guide"]["cards"][0]["title"] == "Second attempt"
    listed = client.get(f"/api/documents/{indexed_document['document']['id']}/guides").json()
    assert listed["pages"] == [4]  # replaced, not added


def test_a_text_only_model_is_not_sent_the_slide_image(
    client: TestClient, indexed_document: dict, writer: FakeWriter
):
    writer.images = False
    guide = client.post(_guide_url(indexed_document), json={"allow_cloud": True}).json()
    assert writer.requests[0].attachments == []
    assert "attached as an image" not in writer.requests[0].user
    assert guide["saw_image"] is False


def test_without_a_model_the_slides_own_text_comes_back_and_is_not_kept(
    client: TestClient, indexed_document: dict
):
    url = _guide_url(indexed_document)
    outline = client.post(url, json={})
    assert outline.status_code == 200, outline.text
    body = outline.json()
    assert body["generated_by"] == "source_outline" and body["saved"] is False
    assert len(body["cards"]) == 1 and "Padding" in body["cards"][0]["markdown"]
    assert any("not an explanation" in warning for warning in body["warnings"])
    # Not kept: choosing a model later gets the slide explained.
    assert client.get(url).json() == {"guide": None}


def test_a_model_that_fails_falls_back_to_the_slides_text(
    client: TestClient, indexed_document: dict, writer: FakeWriter
):
    writer.fail = True
    body = client.post(_guide_url(indexed_document), json={"allow_cloud": True}).json()
    assert body["generated_by"] == "source_outline" and body["saved"] is False
    assert "the model is down" in body["warnings"][0]


def test_a_guide_goes_when_its_document_does(
    client: TestClient, indexed_document: dict, writer: FakeWriter, context
):
    from sqlalchemy import func, select

    from app.storage.models import SlideGuide

    document_id = indexed_document["document"]["id"]
    client.post(_guide_url(indexed_document), json={"allow_cloud": True})
    assert client.delete(f"/api/documents/{document_id}").status_code == 204
    with context.database.session() as session:
        assert session.scalar(select(func.count(SlideGuide.id))) == 0


def test_kept_guides_are_part_of_the_data_export(
    client: TestClient, indexed_document: dict, writer: FakeWriter
):
    client.post(_guide_url(indexed_document), json={"allow_cloud": True})
    exported = client.get("/api/privacy/export").json()["slide_guides"]
    assert [(g["document_id"], g["page_number"]) for g in exported] == [
        (indexed_document["document"]["id"], 4)
    ]
    assert exported[0]["cards"][0]["title"] == "What padding is for"


def test_a_page_that_does_not_exist_is_not_found(client: TestClient, indexed_document: dict):
    assert client.get(_guide_url(indexed_document, page=999)).status_code == 404
    assert client.post(_guide_url(indexed_document, page=999), json={}).status_code == 404
    assert client.get("/api/documents/nope/pages/1/guide").status_code == 404
    assert client.get("/api/documents/nope/guides").status_code == 404


def test_a_question_remembers_the_slide_it_was_asked_on(client: TestClient, indexed_document: dict):
    document_id = indexed_document["document"]["id"]
    answer = client.post(
        "/api/tutor/ask",
        json={"question": "What is padding?", "document_id": document_id, "page_number": 4},
    )
    assert answer.status_code == 200, answer.text
    messages = client.get(f"/api/sessions/{answer.json()['session_id']}/messages").json()
    assert [m["meta"]["page"] for m in messages] == [4, 4]


# --- terms and symbols come first -----------------------------------------------
def test_the_model_is_told_to_define_terms_and_name_symbols_in_tables_first():
    from app.learning.slide_guide import KINDS, SYSTEM

    assert KINDS[0] == "terms"
    assert "the FIRST teaching slide is of kind `terms`" in SYSTEM
    assert "`| Term | What it means |`" in SYSTEM
    assert "`| Symbol | Name | What it means |`" in SYSTEM
    # A bar inside a cell would end the cell, so it is spelled another way.
    assert "\\lvert x \\rvert" in SYSTEM


def test_the_definitions_are_read_first_wherever_the_model_put_them():
    from app.learning.slide_guide import BEYOND_THE_SLIDE, parse_guide

    reply = (
        "=== SLIDE | explanation | Reading the parameters ===\n"
        "The four numbers place one frame against the last.\n\n"
        "=== SLIDE | terms | Terms and symbols ===\n"
        "| Term | What it means |\n|---|---|\n| Joint | A connection between two links. |\n\n"
        "| Symbol | Name | What it means |\n|---|---|---|\n"
        "| $\\theta_i$ | theta sub i | Joint angle (radians). |\n\n"
        "=== SLIDE | check | Check yourself ===\n"
        "```quiz\nQ: What does a joint connect?\nA: Two links.\n```\n"
    )
    cards, _queries = parse_guide(reply)
    assert [card.kind for card in cards] == ["terms", "explanation", "check"]
    assert "| $\\theta_i$ | theta sub i | Joint angle (radians). |" in cards[0].markdown
    # Definitions of what is on the slide are not "beyond the slide".
    assert "terms" not in BEYOND_THE_SLIDE
