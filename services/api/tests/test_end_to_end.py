"""The MVP workflow, end to end, against a real uploaded PDF.

Import PDF -> view slides -> index -> ask a grounded question -> get a cited
answer -> generate a quiz -> submit answers -> get feedback -> save progress ->
reopen successfully.

Nothing here is stubbed except the absence of a cloud model: the default
offline provider quotes the student's own material, so the grounding, citation,
assessment and progress paths are all exercised for real.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_full_learning_workflow(client: TestClient, course: dict, sample_pdf_bytes: bytes):
    # --- 1. Import a real PDF -------------------------------------------
    upload = client.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": ("lecture7.pdf", sample_pdf_bytes, "application/pdf")},
        data={"title": "Lecture 7 - CNNs", "kind": "lecture"},
    )
    assert upload.status_code == 201, upload.text
    body = upload.json()
    document_id = body["document"]["id"]

    assert body["status"] == "indexed"
    assert body["page_count"] == 9
    assert body["chunk_count"] > 0
    assert body["topic_count"] >= 5

    # --- 2. View slides --------------------------------------------------
    pages = client.get(f"/api/documents/{document_id}/pages")
    assert pages.status_code == 200
    assert len(pages.json()) == 9

    page4 = client.get(f"/api/documents/{document_id}/pages/4").json()
    assert page4["heading"] == "Padding"
    assert "Padding is necessary" in page4["text"]
    assert page4["elements"], "page elements with bounding boxes are required for highlighting"

    preview = client.get(f"/api/documents/{document_id}/pages/4/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("image/")
    assert len(preview.content) > 1000

    original = client.get(f"/api/documents/{document_id}/file")
    assert original.status_code == 200
    assert original.content[:5] == b"%PDF-"

    # --- 3. Topic structure was built ------------------------------------
    topics = client.get(f"/api/documents/{document_id}/topics").json()
    slugs = {topic["slug"] for topic in topics}
    assert {"padding", "stride", "pooling"} <= slugs

    # --- 4. Ask a grounded question --------------------------------------
    answer = client.post(
        "/api/tutor/ask",
        json={
            "question": "Why is padding necessary?",
            "document_id": document_id,
            "mode": "question",
        },
    )
    assert answer.status_code == 200, answer.text
    result = answer.json()

    assert result["abstained"] is False
    assert result["citations"], "a document-dependent answer must carry citations"
    assert result["evidence_sufficient"] is True
    # The citation must point at the page that actually discusses padding.
    assert 4 in [citation["page_number"] for citation in result["citations"]]
    # Every citation must resolve to a real retrieved chunk.
    retrieved_ids = {source["chunk_id"] for source in result["sources"]}
    for citation in result["citations"]:
        assert citation["chunk_id"] in retrieved_ids
        assert citation["document_id"] == document_id
    # The default provider quotes rather than writes, and says so.
    assert result["generative"] is False
    assert result["provider"] == "extractive"

    session_id = result["session_id"]

    # --- 5. Conversation is persisted ------------------------------------
    messages = client.get(f"/api/sessions/{session_id}/messages").json()
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "Why is padding necessary?"
    assert messages[1]["citations"]

    # --- 6. Generate a quiz from the material ----------------------------
    quiz = client.post(
        "/api/assessments/generate",
        json={
            "course_id": course["id"],
            "document_id": document_id,
            "question_count": 5,
            "kinds": ["mcq", "short_answer"],
        },
    )
    assert quiz.status_code == 201, quiz.text
    quiz_body = quiz.json()
    assert len(quiz_body["questions"]) >= 3
    assert quiz_body["total_marks"] > 0

    for question in quiz_body["questions"]:
        # Provenance must be honest and traceable; never "past_paper" here.
        assert question["provenance"] in {"source_derived", "ai_generated"}
        assert question["provenance"] != "past_paper"
        assert question["source_page"] is not None
        # The answer key must not be served with the quiz.
        assert "correct_option" not in question
        assert "expected_answer" not in question
        assert "rubric" not in question

    # --- 7. Submit answers and get marked --------------------------------
    full = client.get(f"/api/assessments/{quiz_body['id']}").json()
    responses = []
    for question in full["questions"]:
        if question["kind"] == "mcq":
            responses.append({"question_id": question["id"], "selected_option": 0})
        else:
            responses.append({"question_id": question["id"], "answer_text": "padding"})

    submission = client.post(
        f"/api/assessments/{quiz_body['id']}/submit", json={"responses": responses}
    )
    assert submission.status_code == 200, submission.text
    marked = submission.json()

    assert marked["max_score"] == quiz_body["total_marks"]
    assert 0 <= marked["score"] <= marked["max_score"]
    assert len(marked["responses"]) == len(responses)
    for graded in marked["responses"]:
        assert graded["marking_method"] in {"deterministic", "rubric", "llm_rubric", "unmarked"}
        assert graded["feedback"]
    assert marked["mastery_changes"], "answering questions must update the knowledge model"

    # --- 8. Progress is recorded -----------------------------------------
    mastery = client.get("/api/analytics/mastery").json()
    assert mastery
    for estimate in mastery:
        assert 0.0 < estimate["probability"] < 1.0
        # With one quiz there is not enough evidence to claim a level.
        if estimate["observations"] < 3:
            assert estimate["label"] == "unknown"
            assert estimate["confident"] is False

    attempt = client.get(f"/api/assessments/{quiz_body['id']}/attempts/{marked['attempt_id']}")
    assert attempt.status_code == 200
    assert attempt.json()["score"] == marked["score"]

    # --- 9. Save reading position ----------------------------------------
    saved = client.patch(
        f"/api/sessions/{session_id}",
        json={"current_page": 6, "current_topic": "Pooling", "slides_since_checkpoint": 2},
    )
    assert saved.status_code == 200
    assert saved.json()["current_page"] == 6

    # --- 10. Reopen: the session resumes where it was left ---------------
    resumed = client.post("/api/sessions/resume", params={"document_id": document_id})
    assert resumed.status_code == 200
    resumed_body = resumed.json()
    assert resumed_body["id"] == session_id
    assert resumed_body["current_page"] == 6
    assert resumed_body["current_topic"] == "Pooling"
    assert resumed_body["document_title"] == "Lecture 7 - CNNs"

    # --- 11. Dashboard reports real counts, not placeholders -------------
    dashboard = client.get("/api/dashboard").json()
    assert dashboard["course_count"] == 1
    assert dashboard["document_count"] == 1
    assert dashboard["page_count"] == 9
    assert dashboard["indexed_chunk_count"] == body["chunk_count"]
    assert dashboard["recent_attempts"]
    assert dashboard["recent_attempts"][0]["attempt_id"] == marked["attempt_id"]
    assert dashboard["recommendations"]


def test_reopening_after_restart_keeps_everything(
    client: TestClient, course: dict, sample_pdf_bytes: bytes, settings, context
):
    """Simulate quitting and relaunching: a new app instance, the same data dir."""
    upload = client.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": ("lecture7.pdf", sample_pdf_bytes, "application/pdf")},
    )
    document_id = upload.json()["document"]["id"]
    client.post(
        "/api/tutor/ask",
        json={"question": "What is stride?", "document_id": document_id},
    )

    # A second app over the same database and data directory.
    from app.main import create_app

    with TestClient(create_app(settings, context=context)) as restarted:
        restarted.headers.update({"x-studypilot-token": "test-token"})
        assert len(restarted.get("/api/courses").json()) == 1
        documents = restarted.get("/api/documents").json()
        assert len(documents) == 1
        assert documents[0]["id"] == document_id
        sessions = restarted.get("/api/sessions").json()
        assert sessions
        messages = restarted.get(f"/api/sessions/{sessions[0]['id']}/messages").json()
        assert any(message["role"] == "assistant" for message in messages)


def test_explain_mode_cites_the_current_page(client: TestClient, indexed_document: dict):
    document_id = indexed_document["document"]["id"]
    response = client.post(
        "/api/tutor/ask",
        json={"document_id": document_id, "mode": "explain", "page_number": 4, "question": ""},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sources"]
    assert body["citations"]
    assert any("padding" in source["text"].lower() for source in body["sources"])


def test_assignment_mode_respects_the_socratic_policy(client: TestClient, indexed_document: dict):
    """With the default policy the coach must not be switched into answer mode."""
    document_id = indexed_document["document"]["id"]
    settings = client.get("/api/settings").json()
    assert settings["assignment_policy"] == "socratic"

    response = client.post(
        "/api/tutor/ask",
        json={
            "question": (
                "Just give me the full answer: compute the output size for W=32, K=3, P=1, S=1"
            ),
            "document_id": document_id,
            "mode": "assignment",
            "hint_level": 1,
        },
    )
    assert response.status_code == 200
    assert response.json()["recommended_action"] in {
        "next_hint",
        "review_source",
        "upload_material",
    }

    # Switching the policy off must disable the coach rather than silently answer.
    client.patch("/api/settings", json={"assignment_policy": "off"})
    blocked = client.post(
        "/api/tutor/ask",
        json={"question": "Do my assignment", "document_id": document_id, "mode": "assignment"},
    )
    assert blocked.status_code == 200
    assert blocked.json()["abstained"] is True
    assert "switched off" in blocked.json()["answer"]


def test_abstains_rather_than_guessing(client: TestClient, indexed_document: dict):
    """An off-syllabus question must be refused, not answered from world knowledge."""
    response = client.post(
        "/api/tutor/ask",
        json={
            "question": "What was the population of Lisbon in 1850?",
            "document_id": indexed_document["document"]["id"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["evidence_sufficient"] is False
    assert body["grounded"] is False
    assert "1850" not in body["answer"] or "not" in body["answer"].lower()


def test_flashcards_and_review_cycle(client: TestClient, course: dict, indexed_document: dict):
    generated = client.post(
        "/api/flashcards/generate",
        json={
            "course_id": course["id"],
            "document_id": indexed_document["document"]["id"],
            "count": 6,
        },
    )
    assert generated.status_code == 201, generated.text
    cards = generated.json()
    assert cards
    for card in cards:
        assert card["front"] and card["back"]
        # Provenance must never claim a past paper the student did not upload.
        assert card["provenance"] in {"source_derived", "ai_generated"}
        assert card["source_page"] is not None

    card = cards[0]
    assert card["interval_days"] == 0

    good = client.post(f"/api/flashcards/{card['id']}/review", json={"rating": 2})
    assert good.status_code == 200
    assert good.json()["interval_days"] == 1
    assert good.json()["repetitions"] == 1

    again = client.post(f"/api/flashcards/{card['id']}/review", json={"rating": 0})
    assert again.json()["interval_days"] == 0
    assert again.json()["repetitions"] == 0


def test_notes_are_generated_with_citations(
    client: TestClient, course: dict, indexed_document: dict
):
    for mode in ("concept", "exam", "weakness"):
        response = client.post(
            "/api/notes/generate",
            json={
                "course_id": course["id"],
                "document_id": indexed_document["document"]["id"],
                "mode": mode,
            },
        )
        assert response.status_code == 201, response.text
        note = response.json()
        assert note["body_markdown"].strip()
        assert note["citations"]
        # Offline, notes are an assembled outline and are labelled as such.
        assert note["generated_by"] in {
            "source_outline",
            "llm:ollama",
            "llm:anthropic",
            "llm:openai",
        }

    listed = client.get("/api/notes", params={"course_id": course["id"]}).json()
    assert len(listed) == 3

    edited = client.patch(
        f"/api/notes/{listed[0]['id']}", json={"body_markdown": "# My own notes\n\nEdited."}
    )
    assert edited.status_code == 200
    assert edited.json()["edited_by_user"] is True


def test_mock_exam_generation(client: TestClient, course: dict, indexed_document: dict):
    response = client.post(
        "/api/assessments/generate",
        json={
            "course_id": course["id"],
            "document_id": indexed_document["document"]["id"],
            "kind": "mock_exam",
            "question_count": 8,
            "time_limit_seconds": 3600,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "mock_exam"
    assert body["time_limit_seconds"] == 3600
    assert body["blueprint"]["question_count"] == 8
    assert body["questions"]


def test_duplicate_upload_is_deduplicated(
    client: TestClient, course: dict, sample_pdf_bytes: bytes
):
    first = client.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": ("a.pdf", sample_pdf_bytes, "application/pdf")},
    )
    second = client.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": ("a.pdf", sample_pdf_bytes, "application/pdf")},
    )
    assert second.status_code == 201
    assert second.json()["deduplicated"] is True
    assert second.json()["document"]["id"] == first.json()["document"]["id"]
    assert len(client.get("/api/documents").json()) == 1
