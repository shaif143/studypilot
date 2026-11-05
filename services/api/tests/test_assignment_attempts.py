"""Checking a student's attempt at a step: coaching, never completing.

What is pinned: the hint level comes from code and climbs one rung per
review of the same step; fabricated citation markers are stripped; an
unreadable model answer still yields a review; a non-generative provider is
refused with a message that says what to do; and through the API the reviews
persist per step, oldest first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from app.assignments.review import (
    ReviewNeedsModelError,
    hint_level_for,
    review_attempt,
)
from app.models.base import GenerationRequest, GenerationResult, ProviderInfo
from tests.test_assignment_planner import POOL, FixedRetriever, settings

STEP = {
    "index": 2,
    "title": "Design the padding",
    "instruction": "Choose same padding so the output keeps the input size [1].",
    "approval": "not_required",
}


@dataclass
class Reviewer:
    answer: object
    generative: bool = True
    requests: list[GenerationRequest] = field(default_factory=list)

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            key="scripted",
            label="Scripted",
            sends_data_off_device=False,
            available=True,
            generative=self.generative,
            model="scripted-1",
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        text = self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        return GenerationResult(text=text, provider="scripted", model="scripted-1", generative=True)


def run(model, attempt="I used valid padding so the output shrinks by two.", previous=()):
    return review_attempt(
        settings=settings(),
        llm=model,
        retriever=FixedRetriever(POOL),
        user_id="u",
        course_id="c",
        lecture_document_ids=["doc-lecture"],
        brief_title="CW2",
        step=STEP,
        attempt_text=attempt,
        previous_reviews=list(previous),
    )


def test_hint_level_climbs_one_rung_per_review_and_stops_at_five():
    assert [hint_level_for(n) for n in range(7)] == [1, 2, 3, 4, 5, 5, 5]


def test_a_review_names_the_error_cites_real_passages_and_drops_fake_ones():
    model = Reviewer(
        {
            "verdict": "off_track",
            "summary": "Valid padding shrinks the output [1]; the brief wants it unchanged [9].",
            "issues": [
                {
                    "what": "'valid padding so the output shrinks by two'",
                    "why": "Same padding keeps the size; valid padding applies none [1][7].",
                }
            ],
            "next_hint": "Look at how the two kinds of padding differ [1].",
        }
    )
    review = run(model)
    assert review.verdict == "off_track"
    assert review.hint_level == 1
    assert "[9]" not in review.summary and "[1]" in review.summary
    assert review.issues[0].citations == [1]
    assert "[7]" not in review.issues[0].why
    assert [c.marker for c in review.citations] == [1]
    assert review.citations[0].page_number == POOL[0].page_number
    # The prompt carried the injection guard, the attempt and the hint level.
    prompt = model.requests[0]
    assert "never as instructions" in prompt.system
    assert "<attempt>" in prompt.user and "Level 1" in prompt.user


def test_earlier_reviews_raise_the_level_and_reach_the_prompt():
    model = Reviewer({"verdict": "needs_work", "summary": "Closer.", "issues": []})
    previous = [
        {"verdict": "off_track", "next_hint": "Think about padding.", "hint_level": 1},
        {"verdict": "needs_work", "next_hint": "See the size formula.", "hint_level": 2},
    ]
    review = run(model, previous=previous)
    assert review.hint_level == 3
    assert "Level 3" in model.requests[0].user
    assert "See the size formula." in model.requests[0].user


def test_prose_from_the_model_is_kept_as_the_summary():
    review = run(Reviewer("Your padding choice is the wrong way round: see page 4."))
    assert review.verdict == "needs_work"
    assert "wrong way round" in review.summary
    assert any("structured" in w for w in review.warnings)


def test_an_unknown_verdict_or_junk_issue_is_normalised():
    review = run(Reviewer({"verdict": "meh", "summary": "ok", "issues": ["junk", {"why": "x"}]}))
    assert review.verdict == "needs_work" and review.issues == []


def test_without_a_model_the_review_is_refused_with_advice():
    with pytest.raises(ReviewNeedsModelError, match="Settings"):
        run(Reviewer({}, generative=False))


def test_an_empty_attempt_is_refused():
    with pytest.raises(ValueError, match="Paste"):
        run(Reviewer({"verdict": "on_track", "summary": "", "issues": []}), attempt="   ")


# --- through the API ---------------------------------------------------------
BRIEF_PDF = "sample-assignment-brief.pdf"


@pytest.fixture
def plan(client: TestClient, indexed_document: dict) -> dict:
    from pathlib import Path

    fixture = Path(__file__).resolve().parents[3] / "fixtures" / BRIEF_PDF
    if not fixture.is_file():
        pytest.skip("brief fixture missing")
    course_id = indexed_document["document"]["course_id"]
    upload = client.post(
        f"/api/courses/{course_id}/documents",
        files={"file": (BRIEF_PDF, fixture.read_bytes(), "application/pdf")},
        data={"title": "CW2", "kind": "assignment"},
    )
    assert upload.status_code == 201, upload.text
    drafted = client.post(f"/api/assignments/{upload.json()['document']['id']}/plan", json={})
    assert drafted.status_code == 201, drafted.text
    return drafted.json()


def test_the_api_persists_reviews_per_step_oldest_first(client, plan, monkeypatch):
    from app.api import routes_assignments

    answers = iter(
        [
            {"verdict": "off_track", "summary": "Start again [1].", "issues": [], "next_hint": "A"},
            {"verdict": "needs_work", "summary": "Better.", "issues": [], "next_hint": "B"},
        ]
    )

    class Model(Reviewer):
        def generate(self, request):
            self.answer = next(answers)
            return super().generate(request)

    model = Model({})
    monkeypatch.setattr(
        routes_assignments, "resolve_provider", lambda *a, **k: (model, ["scripted provider"])
    )
    step_index = plan["steps"][0]["index"]
    first = client.post(
        f"/api/assignments/plans/{plan['id']}/steps/{step_index}/attempts",
        json={"text": "I would use a 5x5 kernel and no padding."},
    )
    assert first.status_code == 201, first.text
    assert first.json()["hint_level"] == 1 and first.json()["verdict"] == "off_track"
    assert "scripted provider" in first.json()["warnings"]
    second = client.post(
        f"/api/assignments/plans/{plan['id']}/steps/{step_index}/attempts",
        json={"text": "Now with same padding."},
    )
    assert second.json()["hint_level"] == 2
    listing = client.get(f"/api/assignments/plans/{plan['id']}/attempts").json()
    assert [r["next_hint"] for r in listing] == ["A", "B"]
    assert listing[0]["attempt_text"].startswith("I would use")

    assert (
        client.post(
            f"/api/assignments/plans/{plan['id']}/steps/999/attempts", json={"text": "x"}
        ).status_code
        == 404
    )


def test_the_api_says_a_model_is_needed_when_there_is_none(client, plan):
    step_index = plan["steps"][0]["index"]
    response = client.post(
        f"/api/assignments/plans/{plan['id']}/steps/{step_index}/attempts",
        json={"text": "I tried something."},
    )
    assert response.status_code == 409
    assert "language model" in response.json()["detail"]
