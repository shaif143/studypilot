"""Past papers: what is read from them, and how practice then follows them.

The analyst and the examiner are a fake model, so what is tested is what the
code decides for itself: that a forecast is clamped to what the number of
papers can support, that practice is written from this year's lectures and
never from the papers themselves, that the paper's own question forms are
used, and that a question is only called a past-paper variation when the
model named the question it built on.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.api import routes_exams
from app.assessment import generator
from app.assessment import service as assessment_service
from app.learning import exam_analysis
from app.models.base import GenerationRequest, GenerationResult, ProviderInfo

MCQ_PAPER = """Module CS3410 Examination 2023. Time allowed: 2 hours.
1. Which padding keeps the output size?
A. valid
B. same
C. full
D. none
2. What does stride control?
A. depth
B. step size
C. kernel size
D. bias
"""

WRITTEN_PAPER = """CS3410 Examination 2022. Time allowed: 90 minutes.
Question 1
(a) Derive the output size of a convolution layer. [10 marks]
(b) Calculate the output width for n = 28, k = 3, p = 1, s = 1. [5 marks]
Question 2
Explain why padding is needed and discuss two kinds. [15 marks]
"""

PROFILE = {
    "papers_read": [{"title": "Exam 2022", "year": "2022", "questions": 2}],
    "format": {
        "style": "written",
        "summary": "Two written questions, answer both.",
        "duration_minutes": 90,
        "total_marks": 30,
        "sections": [
            {
                "name": "A",
                "instructions": "answer all",
                "questions": 2,
                "marks_each": 15,
                "form": "explanation",
            }
        ],
    },
    "question_mix": {"mcq": 0, "short_answer": 0.05, "numeric": 0.35, "explanation": 0.6},
    "command_words": ["derive", "calculate", "explain"],
    "topics": [
        {
            "topic": "Padding",
            "likelihood": 1.0,
            "pattern": "every paper",
            "appearances": ["2022 Q2"],
            "typical_marks": 15,
            "form": "explanation",
            "rationale": "In the one paper supplied.",
            "next_variation": "Compare three kinds.",
        },
        {"topic": "Stride", "likelihood": 0.4, "appearances": [], "form": "poem"},
        {"topic": "", "likelihood": 0.9},
    ],
    "patterns": ["Derivation then a calculation."],
    "marking_habits": ["One mark per step shown."],
    "advice": ["Practise the output-size formula."],
    "confidence": "high",
    "caveat": "One paper only.",
}


def _chunk(chunk_id: str):
    from app.rag.retriever import RetrievedChunk

    return RetrievedChunk(
        chunk_id=chunk_id,
        text="Padding adds a border.",
        page_number=4,
        document_id="d",
        document_title="L7",
        section="Padding",
        topic_slug="padding",
        element_type="text",
        bbox=[],
        score=1.0,
        retrievers=["scan"],
        parent_text="Padding adds a border.",
    )


def _pdf(text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((60, 80), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


@dataclass
class FakeModel:
    """Answers as the analyst or as the examiner, by which it was asked to be."""

    generative: bool = True
    requests: list[GenerationRequest] = field(default_factory=list)
    past_paper_ref: str = "2022 Q2"

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            key="fake",
            label="Fake",
            sends_data_off_device=True,
            available=True,
            generative=self.generative,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        if request.system == exam_analysis.ANALYST_SYSTEM:
            text = json.dumps(PROFILE)
        else:
            chunk_ids = re.findall(r"chunk_id=([0-9a-f]+)", request.user)
            wanted = int(re.search(r"Write exactly (\d+) questions", request.user).group(1))
            questions = [
                {
                    "kind": "explanation",
                    "prompt": f"(a) Explain why padding is needed, variation {index}. [15 marks]",
                    "expected_answer": "Padding keeps borders and size.",
                    "rubric_points": ["keeps size", "keeps border information"],
                    "marks": 15,
                    "difficulty": "medium",
                    "source_chunk_id": chunk_ids[index % len(chunk_ids)],
                    "explanation": "From the padding slide.",
                    "past_paper_ref": self.past_paper_ref if index == 0 else "",
                }
                for index in range(wanted)
            ]
            text = json.dumps({"questions": questions})
        return GenerationResult(text=text, provider="fake", model="fake-1", generative=True)


@pytest.fixture
def model(monkeypatch) -> FakeModel:
    fake = FakeModel()
    monkeypatch.setattr(routes_exams, "resolve_provider", lambda *a, **k: (fake, []))
    monkeypatch.setattr(assessment_service, "resolve_provider", lambda *a, **k: (fake, []))
    return fake


def _add_paper(client: TestClient, course: dict, name: str, text: str) -> dict:
    response = client.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": (name, _pdf(text), "application/pdf")},
        data={"kind": "past_paper"},
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- what is decided without a model -----------------------------------------------------
def test_the_format_of_a_paper_is_counted_from_its_text():
    mcq = exam_analysis.count_profile([exam_analysis.Paper("d1", "2023", MCQ_PAPER)])
    assert mcq["format"]["style"] == "mcq" and mcq["question_mix"]["mcq"] > 0.7
    assert mcq["format"]["duration_minutes"] == 120
    assert mcq["topics"] == [] and mcq["confidence"] == "low"

    written = exam_analysis.count_profile([exam_analysis.Paper("d2", "2022", WRITTEN_PAPER)])
    assert written["format"]["style"] == "written" and written["question_mix"]["mcq"] == 0
    assert written["format"]["total_marks"] == 30 and written["format"]["duration_minutes"] == 90
    assert abs(sum(written["question_mix"].values()) - 1) < 0.02
    assert exam_analysis.kinds_from_profile(written)[0] in {"explanation", "numeric"}
    assert "mcq" not in exam_analysis.kinds_from_profile(written)


def test_a_forecast_is_clamped_to_what_the_papers_can_support():
    one = exam_analysis.clean_profile(PROFILE, paper_count=1)
    assert one is not None
    # One paper: nothing above 70%, and never "high" confidence, whatever the model said.
    assert [topic["likelihood"] for topic in one["topics"]] == [0.7, 0.4]
    assert one["confidence"] == "low"
    # A nameless topic is dropped and an unknown question form is blanked.
    assert [topic["topic"] for topic in one["topics"]] == ["Padding", "Stride"]
    assert one["topics"][1]["form"] == ""

    three = exam_analysis.clean_profile(PROFILE, paper_count=3)
    assert three["topics"][0]["likelihood"] == 0.95  # never certain
    assert three["confidence"] == "high"
    assert abs(sum(three["question_mix"].values()) - 1) < 0.01

    assert exam_analysis.clean_profile("not an object", paper_count=2) is None
    assert exam_analysis.clean_profile({"topics": "x", "format": 3}, paper_count=2) is None


def test_a_passage_is_weighed_by_the_topics_likely_to_be_asked():
    profile = exam_analysis.clean_profile(PROFILE, paper_count=3)
    about = exam_analysis.exam_weight("Padding adds a border of zeros.", profile)
    aside = exam_analysis.exam_weight("Batch normalisation rescales activations.", profile)
    assert about > 0.9 and aside == 0
    brief = exam_analysis.style_brief(profile, sample="Q1 Derive the output size. [10 marks]")
    assert "Padding (95%)" in brief and "Likely next: Compare three kinds." in brief
    assert "explanation 60%" in brief and "derive, calculate, explain" in brief
    assert "do not copy them" in brief


# --- through the API ---------------------------------------------------------------------
def test_papers_are_analysed_and_the_analysis_is_kept(
    client: TestClient, course: dict, indexed_document: dict, model: FakeModel
):
    url = f"/api/courses/{course['id']}/exam-profile"
    empty = client.get(url).json()
    assert empty["papers"] == [] and empty["profile"] is None and empty["stale"] is False
    assert client.post(url, json={"allow_cloud": True}).status_code == 422  # nothing to read

    _add_paper(client, course, "exam-2022.pdf", WRITTEN_PAPER)
    listed = client.get(url).json()
    assert [paper["title"] for paper in listed["papers"]] == ["exam-2022"]
    assert listed["profile"] is None and len(model.requests) == 0  # reading calls no model

    analysed = client.post(url, json={"allow_cloud": True})
    assert analysed.status_code == 200, analysed.text
    body = analysed.json()
    assert body["generated_by"] == "llm:fake" and body["stale"] is False
    assert body["profile"]["topics"][0] == {
        "topic": "Padding",
        "likelihood": 0.7,
        "pattern": "every paper",
        "appearances": ["2022 Q2"],
        "typical_marks": 15,
        "form": "explanation",
        "rationale": "In the one paper supplied.",
        "next_variation": "Compare three kinds.",
    }
    # The analyst saw the paper and this year's lectures, not the other way round.
    prompt = model.requests[0].user
    assert "Derive the output size" in prompt and "Lecture 7 - CNNs" in prompt
    assert "exam-2022" in prompt.split("This year's lectures:")[1].split("The past papers:")[1]
    assert "exam-2022" not in prompt.split("This year's lectures:")[1].split("Topics")[0]

    # A paper added afterwards makes the kept analysis out of date.
    _add_paper(client, course, "exam-2023.pdf", MCQ_PAPER)
    assert client.get(url).json()["stale"] is True
    assert len(client.get("/api/privacy/export").json()["exam_profiles"]) == 1

    forgotten = client.delete(url).json()
    assert forgotten["profile"] is None and len(forgotten["papers"]) == 2
    assert client.get("/api/courses/nope/exam-profile").status_code == 404


def test_without_a_model_only_the_format_is_counted(
    client: TestClient, course: dict, model: FakeModel
):
    model.generative = False
    _add_paper(client, course, "exam-2023.pdf", MCQ_PAPER)
    body = client.post(f"/api/courses/{course['id']}/exam-profile", json={}).json()
    assert body["generated_by"] == "counted" and body["profile"]["topics"] == []
    assert body["profile"]["format"]["style"] == "mcq"
    assert any("only their format was counted" in warning for warning in body["warnings"])
    assert model.requests == []


def test_practice_follows_the_analysed_papers(
    client: TestClient, course: dict, indexed_document: dict, model: FakeModel
):
    paper = _add_paper(client, course, "exam-2022.pdf", WRITTEN_PAPER)
    client.post(f"/api/courses/{course['id']}/exam-profile", json={"allow_cloud": True})

    made = client.post(
        "/api/assessments/generate",
        json={
            "course_id": course["id"],
            "question_count": 3,
            "kind": "mock_exam",
            "kinds": ["mcq"],
            "allow_cloud": True,
        },
    )
    assert made.status_code == 201, made.text
    exam = made.json()
    assert exam["title"].startswith("Exam-style mock exam")
    # The paper's own forms, not the "mcq" the request asked for.
    assert {question["kind"] for question in exam["questions"]} == {"explanation"}
    # Only the question that named the one it was modelled on is called a variation.
    assert [question["provenance"] for question in exam["questions"]] == [
        "past_paper_variation",
        "ai_generated",
        "ai_generated",
    ]
    varied = generator.QuestionGenerator._validate_llm_question(
        {
            "kind": "explanation",
            "prompt": "Explain padding in a CNN.",
            "expected_answer": "x",
            "source_chunk_id": "c1",
            "explanation": "Why.",
            "past_paper_ref": "2022 Q2",
        },
        {"c1": _chunk("c1")},
        exam=True,
    )
    assert varied.explanation.endswith("Modelled on 2022 Q2.")
    # Outside exam mode nothing can be called a variation, whatever the model sends.
    plain_question = generator.QuestionGenerator._validate_llm_question(
        {
            "kind": "explanation",
            "prompt": "Explain padding in a CNN.",
            "expected_answer": "x",
            "source_chunk_id": "c1",
            "past_paper_ref": "2022 Q2",
        },
        {"c1": _chunk("c1")},
    )
    assert plain_question.provenance == "ai_generated"

    request = model.requests[-1]
    assert request.system == generator.EXAMINER_SYSTEM
    assert "THE REAL EXAM" in request.user and "Padding (70%)" in request.user
    # Written from this year's lectures: the paper's own passages are not the material.
    material = request.user.split("This year's lecture material:")[1]
    paper_chunk_pages = client.get(f"/api/documents/{paper['document']['id']}/pages/1").json()
    assert paper_chunk_pages["text"][:40] not in material
    assert "Padding" in material

    # The next exam is told what was already asked, so it asks something else.
    client.post(
        "/api/assessments/generate",
        json={"course_id": course["id"], "question_count": 2, "allow_cloud": True},
    )
    assert "variation 0" in model.requests[-1].user.split("already been given")[1]

    # Switched off for one go, the request's own choice stands.
    plain = client.post(
        "/api/assessments/generate",
        json={
            "course_id": course["id"],
            "question_count": 2,
            "kinds": ["explanation"],
            "allow_cloud": True,
            "exam_style": False,
        },
    ).json()
    assert not plain["title"].startswith("Exam-style")
    assert model.requests[-1].system != generator.EXAMINER_SYSTEM


def test_flashcards_take_the_form_of_a_multiple_choice_exam(
    client: TestClient, course: dict, indexed_document: dict, monkeypatch
):
    @dataclass
    class Examiner(FakeModel):
        def generate(self, request: GenerationRequest) -> GenerationResult:
            self.requests.append(request)
            chunk_id = re.findall(r"chunk_id=([0-9a-f]+)", request.user)[0]
            question = {
                "kind": "mcq",
                "prompt": "Which padding keeps the output the same size?",
                "options": ["valid", "same", "full", "none"],
                "correct_option": 1,
                "source_chunk_id": chunk_id,
                "explanation": "Same padding adds a border.",
            }
            return GenerationResult(
                text=json.dumps({"questions": [question]}),
                provider="fake",
                model="fake-1",
                generative=True,
            )

    fake = Examiner()
    monkeypatch.setattr(assessment_service, "resolve_provider", lambda *a, **k: (fake, []))
    monkeypatch.setattr(routes_exams, "resolve_provider", lambda *a, **k: (FakeModel(False), []))
    _add_paper(client, course, "exam-2023.pdf", MCQ_PAPER)
    client.post(f"/api/courses/{course['id']}/exam-profile", json={})  # counted: an MCQ paper

    cards = client.post(
        "/api/flashcards/generate",
        json={"course_id": course["id"], "count": 1, "allow_cloud": True},
    )
    assert cards.status_code == 201, cards.text
    card = cards.json()[0]
    assert card["front"].endswith("A. valid\nB. same\nC. full\nD. none")
    assert card["back"].startswith("B. same") and "adds a border" in card["back"]
