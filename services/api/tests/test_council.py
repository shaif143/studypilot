"""Models working an assignment out together.

The models are scripted, so what is tested is everything the discussion
decides for itself: who may judge a dispute, what counts as settled, when it
ends, and what is said when the models did not all agree. A scripted model
answers by which stage's instructions it was given, exactly as a real one is
told what stage it is in.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from app.council import engine, prompts
from app.council import service as council_service
from app.models.base import (
    GenerationRequest,
    GenerationResult,
    ProviderInfo,
    ProviderUnavailableError,
)


@dataclass
class Scripted:
    """A model that answers each stage from a script, and records what it was asked."""

    name: str
    draft: str = "A draft."
    review: str = "=== BEST ===\ndraft: Claude\nbecause: it is complete.\n"
    defence: str = ""
    ruling: str = ""
    synthesis: str = "The agreed version."
    votes: list[str] = field(default_factory=lambda: ["=== VOTE ===\nvote: agree\n"])
    revision: str = "The revised version."
    fail_at: str = ""
    asked: list[tuple[str, str]] = field(default_factory=list)

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            key=self.name.lower(), label=self.name, sends_data_off_device=False, available=True
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        stage = next(
            label
            for label, marker in (
                ("propose", "YOUR OWN DRAFT"),
                ("review", "STAGE 2 OF 5"),
                ("defend", "ANSWER YOUR REVIEWERS"),
                ("judge", "STAGE 3 OF 5: JUDGE"),
                ("synthesis", "THE AGREED VERSION"),
                ("vote", "DO YOU ACCEPT IT"),
                ("revise", "STAGE 5 OF 5: REVISE"),
            )
            if marker in request.system
        )
        self.asked.append((stage, request.user))
        if stage == self.fail_at:
            raise ProviderUnavailableError(f"{self.name} is down")
        if stage == "vote":
            text = self.votes.pop(0) if len(self.votes) > 1 else self.votes[0]
        else:
            text = {
                "propose": self.draft,
                "review": self.review,
                "defend": self.defence,
                "judge": self.ruling,
                "synthesis": self.synthesis,
                "revise": self.revision,
            }[stage]
        return GenerationResult(text=text, provider=self.name, model="scripted", generative=True)

    def stages(self) -> list[str]:
        return [stage for stage, _ in self.asked]


def _members(*models: Scripted) -> list[engine.Member]:
    return [engine.Member(key=m.name.lower(), name=m.name, provider=m) for m in models]


TASK = engine.Task(
    kind="guide",
    course_title="Deep Learning",
    brief_title="Coursework 1",
    brief_text="Compute the output width of a convolution with n=32, k=5, p=2, s=2.",
    passages=[
        {
            "marker": 1,
            "text": "W_out = floor((W - K + 2P)/S) + 1",
            "document_title": "L7",
            "page_number": 5,
        }
    ],
)

ISSUE_ABOUT_GEMINI = (
    "=== ISSUE ===\ndraft: gemini\nkind: mistake\nwhere: the answer is 15\n"
    "problem: The floor is taken but the +1 is missing.\n"
    "reason: The formula in [1] ends with + 1, so\nfloor(15.5) + 1 = 16.\n"
    "fix: The output width is 16.\n\n"
    "=== BEST ===\ndraft: Claude\nbecause: it applies [1] fully.\n"
)


def _defend(reason: str) -> str:
    return f"=== RESPONSE ===\nissue: 1\nstance: defend\nreason: {reason}\n"


def _run(members, **kwargs):
    events: list[dict] = []
    outcome = engine.Discussion(TASK, members, emit=events.append, **kwargs).run()
    return outcome, events


# --- reading replies ---------------------------------------------------------------------
def test_a_reply_is_read_by_its_blocks_and_fields_across_lines():
    blocks = engine.parse_blocks(
        ISSUE_ABOUT_GEMINI, ("draft", "kind", "where", "problem", "reason", "fix", "because")
    )
    assert [name for name, _ in blocks] == ["ISSUE", "BEST"]
    issue = blocks[0][1]
    # A reason runs over two lines and holds a colon-free continuation.
    assert issue["reason"].endswith("floor(15.5) + 1 = 16.") and "\n" in issue["reason"]
    assert issue["fix"] == "The output width is 16."
    assert engine.match_name("**Gemini**", ["Claude", "Gemini"]) == "Gemini"
    assert engine.match_name("the local model's", ["Claude", "Local model"]) == "Local model"
    assert engine.match_name("nobody", ["Claude", "Gemini"]) == ""


# --- the discussion ----------------------------------------------------------------------
def test_an_accepted_issue_is_settled_without_a_judge():
    claude = Scripted("Claude", review=ISSUE_ABOUT_GEMINI, synthesis="Width is 16.")
    gemini = Scripted(
        "Gemini",
        draft="the answer is 15",
        review="=== BEST ===\ndraft: Claude\nbecause: correct.\n",
        defence="=== RESPONSE ===\nissue: 1\nstance: accept\nreason: I dropped the + 1.\n",
    )
    chatgpt = Scripted("ChatGPT", review="=== BEST ===\ndraft: Claude\nbecause: correct.\n")
    outcome, events = _run(_members(claude, chatgpt, gemini))

    assert outcome.unanimous and outcome.text == "Width is 16." and outcome.lead == "Claude"
    assert outcome.agreed == ["Claude", "ChatGPT", "Gemini"] and outcome.rounds == 1
    assert [issue.status for issue in outcome.issues] == ["accepted"]
    # No one was asked to judge: there was nothing left in dispute.
    assert "judge" not in claude.stages() + chatgpt.stages() + gemini.stages()
    # The settled correction was in front of whoever wrote the agreed version.
    synthesis_prompt = next(user for stage, user in claude.asked if stage == "synthesis")
    assert (
        "SETTLED corrections" in synthesis_prompt and "The output width is 16." in synthesis_prompt
    )
    assert outcome.consensus()["issues_accepted"] == 1
    assert [e["type"] for e in events if e["type"] != "phase"][:3] == ["draft", "draft", "draft"]


def test_a_defended_issue_is_ruled_on_by_a_model_that_took_neither_side():
    claude = Scripted("Claude", review=ISSUE_ABOUT_GEMINI)
    gemini = Scripted(
        "Gemini",
        draft="the answer is 15",
        review="=== BEST ===\ndraft: Gemini\nbecause: mine.\n",
        defence=_defend("The floor already rounds it."),
    )
    chatgpt = Scripted(
        "ChatGPT",
        review="=== BEST ===\ndraft: Claude\nbecause: correct.\n",
        ruling="=== RULING ===\nissue: 1\nruling: uphold\nreason: [1] ends in + 1; 15 + 1 = 16.\n",
    )
    outcome, events = _run(_members(claude, chatgpt, gemini))

    issue = outcome.issues[0]
    assert (issue.reviewer, issue.author, issue.judge, issue.status) == (
        "Claude",
        "Gemini",
        "ChatGPT",
        "upheld",
    )
    # Only the neutral model judged, and it saw both the issue and the defence.
    assert "judge" in chatgpt.stages()
    assert "judge" not in claude.stages() and "judge" not in gemini.stages()
    case = next(user for stage, user in chatgpt.asked if stage == "judge")
    assert "the +1 is missing" in case and "The floor already rounds it." in case
    assert outcome.consensus()["issues_upheld"] == 1
    ruling = next(e for e in events if e["type"] == "ruling")
    assert ruling["member"] == "ChatGPT" and ruling["rulings"][0]["ruling"] == "upheld"


def test_a_dismissed_issue_is_not_applied_and_two_models_have_no_judge():
    claude = Scripted("Claude", review=ISSUE_ABOUT_GEMINI)
    gemini = Scripted(
        "Gemini",
        review="=== BEST ===\ndraft: Claude\nbecause: fine.\n",
        defence="=== RESPONSE ===\nissue: 1\nstance: defend\nreason: I wrote 16, not 15.\n",
    )
    chatgpt = Scripted(
        "ChatGPT", ruling="=== RULING ===\nissue: 1\nruling: dismiss\nreason: The draft says 16.\n"
    )
    outcome, _ = _run(_members(claude, chatgpt, gemini))
    assert outcome.issues[0].status == "dismissed"
    prompt = next(user for stage, user in claude.asked if stage == "synthesis")
    dismissed = prompt.split("DISMISSED points, not to apply:")[1].split("CONTESTED")[0]
    assert "the +1 is missing" in dismissed
    assert "(none)" in prompt.split("SETTLED corrections, to apply:")[1].split("DISMISSED")[0]

    # With only two models there is no third to rule: it stays contested, and
    # whoever writes the agreed version is told to decide it on its merits.
    claude2 = Scripted("Claude", review=ISSUE_ABOUT_GEMINI)
    gemini2 = Scripted(
        "Gemini",
        review="=== BEST ===\ndraft: Claude\nbecause: fine.\n",
        defence="=== RESPONSE ===\nissue: 1\nstance: defend\nreason: I wrote 16.\n",
    )
    two, _ = _run(_members(claude2, gemini2))
    assert two.issues[0].status == "contested" and two.issues[0].judge == ""
    assert "judge" not in claude2.stages() + gemini2.stages()
    assert (
        "I wrote 16." in next(u for s, u in claude2.asked if s == "synthesis").split("CONTESTED")[1]
    )


def test_what_is_not_an_issue_is_not_counted():
    def issue(draft: str, kind: str, problem: str, reason: str) -> str:
        why = f"reason: {reason}\n" if reason else ""
        return f"=== ISSUE ===\ndraft: {draft}\nkind: {kind}\nproblem: {problem}\n{why}fix: y\n"

    noise = (
        issue("Claude", "mistake", "my own draft is wrong", "x")
        + issue("Nobody", "mistake", "unknown author", "x")
        + issue("Gemini", "mistake", "no reason is given", "")
        + issue("Gemini", "better", "a clearer order", "[1]")
    )
    claude = Scripted("Claude", review=noise)
    gemini = Scripted(
        "Gemini", defence="=== RESPONSE ===\nissue: 1\nstance: accept\nreason: fair.\n"
    )
    outcome, _ = _run(_members(claude, gemini))
    # Its own draft, an unknown author and a claim with no reason are all dropped.
    kept = [(found.kind, found.problem) for found in outcome.issues]
    assert kept == [("better", "a clearer order")]


def test_objections_send_it_back_until_everyone_agrees():
    objection = (
        "=== VOTE ===\nvote: object\n\n=== OBJECTION ===\nwhere: width is 15\n"
        "problem: Still 15.\nreason: [1] gives 16.\nfix: Write 16.\n"
    )
    claude = Scripted("Claude", synthesis="width is 15", revision="width is 16")
    gemini = Scripted("Gemini", votes=[objection, "=== VOTE ===\nvote: agree\n"])
    chatgpt = Scripted("ChatGPT")
    outcome, events = _run(_members(claude, chatgpt, gemini))

    assert outcome.unanimous and outcome.text == "width is 16" and outcome.rounds == 2
    assert claude.stages().count("revise") == 1
    assert "Still 15." in next(user for stage, user in claude.asked if stage == "revise")
    votes = [(e["member"], e["round"], e["vote"]) for e in events if e["type"] == "vote"]
    assert ("Gemini", 1, "object") in votes and ("Gemini", 2, "agree") in votes


def test_when_they_never_agree_the_result_says_so_and_keeps_the_objections():
    objection = (
        "=== VOTE ===\nvote: object\n\n=== OBJECTION ===\nwhere: step 3\n"
        "problem: Wrong method.\nreason: Not in [1].\nfix: Use the formula.\n"
    )
    claude = Scripted("Claude")
    gemini = Scripted("Gemini", votes=[objection])
    chatgpt = Scripted("ChatGPT")
    outcome, _ = _run(_members(claude, chatgpt, gemini))

    assert outcome.unanimous is False and outcome.rounds == engine.MAX_VOTE_ROUNDS
    assert outcome.agreed == ["Claude", "ChatGPT"]
    assert [o["member"] for o in outcome.objections] == ["Gemini"]
    assert outcome.objections[0]["fix"] == "Use the formula."
    # Revised between votes, but not after the last one.
    assert claude.stages().count("revise") == engine.MAX_VOTE_ROUNDS - 1


def test_a_vote_that_cannot_be_read_is_not_agreement():
    claude = Scripted("Claude")
    gemini = Scripted("Gemini", votes=["I think this looks great overall!"])
    outcome, _ = _run(_members(claude, gemini))
    assert outcome.unanimous is False and outcome.agreed == ["Claude"]
    assert "gave no objection that could be read" in outcome.objections[0]["problem"]

    # "agree" with an objection attached is an objection.
    mixed = (
        "=== VOTE ===\nvote: agree\n"
        "=== OBJECTION ===\nproblem: but step 2 is wrong\nreason: [1]\nfix: redo\n"
    )
    outcome, _ = _run(_members(Scripted("Claude"), Scripted("Gemini", votes=[mixed])))
    assert outcome.unanimous is False


def test_a_model_that_fails_is_left_out_and_said_to_be():
    claude = Scripted("Claude")
    gemini = Scripted("Gemini", fail_at="propose")
    chatgpt = Scripted("ChatGPT", fail_at="vote")
    outcome, events = _run(_members(claude, chatgpt, gemini))

    assert outcome.took_part == ["Claude", "ChatGPT"]
    # ChatGPT could not vote, so its silence is not counted as agreement.
    assert outcome.unanimous is False and outcome.agreed == ["Claude"]
    assert {(d["member"], d["phase"]) for d in outcome.dropped} >= {
        ("Gemini", "propose"),
        ("ChatGPT", "vote"),
    }
    assert any(e["type"] == "error" and e["member"] == "Gemini" for e in events)
    # Gemini wrote no draft, so it reviews no one and is reviewed by no one.
    assert gemini.stages() == ["propose"]

    alone, _ = _run(_members(Scripted("Claude"), Scripted("Gemini", fail_at="propose")))
    assert alone.unanimous is False and alone.rounds == 0 and alone.agreed == ["Claude"]
    with pytest.raises(engine.NoAnswerError):
        _run(_members(Scripted("Claude", fail_at="propose")))


def test_the_draft_voted_best_is_built_on_and_a_tie_goes_to_the_first_seat():
    vote_gemini = "=== BEST ===\ndraft: Gemini\nbecause: clearest.\n"
    claude = Scripted("Claude", review=vote_gemini)
    chatgpt = Scripted("ChatGPT", review=vote_gemini)
    gemini = Scripted("Gemini", review="=== BEST ===\ndraft: Claude\nbecause: x.\n", synthesis="G")
    outcome, _ = _run(_members(claude, chatgpt, gemini))
    assert outcome.lead == "Gemini" and outcome.text == "G"

    tied, _ = _run(
        _members(
            Scripted("Claude", review="=== BEST ===\ndraft: Gemini\nbecause: x.\n", synthesis="C"),
            Scripted("Gemini", review="=== BEST ===\ndraft: Claude\nbecause: x.\n", synthesis="G"),
        )
    )
    assert tied.lead == "Claude"


def test_a_discussion_can_be_stopped_between_stages():
    stop = {"now": False}

    def emit(event):
        if event.get("type") == "review":
            stop["now"] = True

    claude, gemini = Scripted("Claude"), Scripted("Gemini")
    with pytest.raises(engine.DiscussionStoppedError):
        engine.Discussion(
            TASK, _members(claude, gemini), emit=emit, should_stop=lambda: stop["now"]
        ).run()
    assert "synthesis" not in claude.stages() and "vote" not in gemini.stages()


def test_a_local_model_is_given_less_to_read_and_the_task_names_what_is_asked():
    passages = [
        {"marker": i, "text": "x" * 2000, "document_title": "L", "page_number": i}
        for i in range(1, 20)
    ]
    task = engine.Task("solution", "C", "CW", "brief " * 50, passages, guide="1. Do this.")
    cloud = task.block(engine.Member("anthropic", "Claude", None))
    local = task.block(engine.Member("ollama", "Local model", None, local=True))
    assert len(local) < engine.LOCAL_BUDGET + 2500 < len(cloud)
    assert "[1] L, page 1" in local and "1. Do this." in local
    assert "complete the assignment" in task.instructions
    assert "step-by-step guide" in engine.Task("guide", "", "", "", []).instructions
    for system in (prompts.PROPOSE_SYSTEM, prompts.SYNTHESIS_SYSTEM, prompts.REVISE_SYSTEM):
        assert "{task}" in system


# --- through the API ---------------------------------------------------------------------
@pytest.fixture
def seated(monkeypatch):
    """Three scripted models in the seats, whatever is configured on this machine."""
    models = {
        "anthropic": Scripted("Claude", review=ISSUE_ABOUT_GEMINI, synthesis="Use [1]: width 16."),
        "openai": Scripted(
            "ChatGPT",
            ruling="=== RULING ===\nissue: 1\nruling: uphold\nreason: [1] ends in + 1.\n",
        ),
        "gemini": Scripted(
            "Gemini",
            draft="the answer is 15",
            defence="=== RESPONSE ===\nissue: 1\nstance: defend\nreason: floor rounds it.\n",
        ),
    }

    def seats(settings, user_settings, *, allow_cloud):
        return [
            {
                "key": key,
                "name": name,
                "local": key == "ollama",
                "ready": key in models and (allow_cloud or key == "ollama"),
                "model": "scripted" if key in models else "",
                "detail": "" if key in models else "Not running.",
                "provider": models.get(key),
            }
            for key, name in council_service.SEATS
        ]

    monkeypatch.setattr(council_service, "describe_seats", seats)
    return models


def _brief(client: TestClient, course: dict, sample_pdf_bytes: bytes) -> dict:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text(
        (60, 80), "Coursework 1\nQ1. Explain why padding is necessary in a CNN. [10 marks]"
    )
    response = client.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": ("coursework-1.pdf", doc.tobytes(), "application/pdf")},
        data={"kind": "assignment"},
    )
    assert response.status_code == 201, response.text
    return response.json()["document"]


def _wait(client: TestClient, document_id: str, kind: str) -> dict:
    deadline = time.time() + 20
    while time.time() < deadline:
        state = client.get(f"/api/assignments/{document_id}/council").json()
        if state[kind] and state[kind]["status"] not in {"queued", "running"}:
            return state
        time.sleep(0.05)
    raise AssertionError("the discussion did not finish")


def test_a_guide_is_discussed_then_the_assignment_is_completed_by_it(
    client: TestClient, course: dict, indexed_document: dict, sample_pdf_bytes: bytes, seated
):
    brief = _brief(client, course, sample_pdf_bytes)
    url = f"/api/assignments/{brief['id']}/council"

    state = client.get(url, params={"allow_cloud": True}).json()
    assert [(s["name"], s["ready"]) for s in state["seats"]] == [
        ("Claude", True),
        ("ChatGPT", True),
        ("Gemini", True),
        ("Local model", False),
    ]
    assert state["guide"] is None and state["solution"] is None

    # The assignment cannot be completed before there is a guide to follow.
    early = client.post(f"{url}/solution", json={"allow_cloud": True})
    assert early.status_code == 409 and "guide" in early.json()["detail"]

    started = client.post(
        f"{url}/guide",
        json={"allow_cloud": True, "reference_document_ids": [indexed_document["document"]["id"]]},
    )
    assert started.status_code == 202, started.text
    guide = _wait(client, brief["id"], "guide")["guide"]
    assert guide["status"] == "done" and guide["result_markdown"] == "Use [1]: width 16."
    assert [m["name"] for m in guide["members"]] == ["Claude", "ChatGPT", "Gemini"]
    assert guide["consensus"]["unanimous"] is True and guide["consensus"]["issues_upheld"] == 1
    assert guide["reference_document_ids"] == [indexed_document["document"]["id"]]
    # The numbered passages came from the chosen lecture, and can be opened.
    assert guide["citations"] and guide["citations"][0]["marker"] == 1
    assert {c["document_id"] for c in guide["citations"]} == {indexed_document["document"]["id"]}
    assert "text" not in guide["citations"][0]
    kinds = [event["type"] for event in guide["events"]]
    assert kinds.count("draft") == 3 and "ruling" in kinds and kinds[-1] == "issues"
    # The models were given the brief and the passages.
    asked = seated["anthropic"].asked[0][1]
    assert "padding is necessary" in asked and "[1] Lecture 7 - CNNs" in asked

    done = client.post(f"{url}/solution", json={"allow_cloud": True})
    assert done.status_code == 202, done.text
    solution = _wait(client, brief["id"], "solution")["solution"]
    assert solution["status"] == "done"
    solving = next(u for s, u in seated["openai"].asked[::-1] if s == "propose")
    # Completing it means following the guide that was agreed.
    assert "The step-by-step guide all models agreed on" in solving and "width 16" in solving
    # With no references chosen, every lecture in the course is used, never the brief.
    assert brief["id"] not in solution["reference_document_ids"]
    assert indexed_document["document"]["id"] in solution["reference_document_ids"]


def test_a_discussion_needs_two_models_and_an_assignment_brief(
    client: TestClient, course: dict, indexed_document: dict, sample_pdf_bytes: bytes, seated
):
    brief = _brief(client, course, sample_pdf_bytes)
    url = f"/api/assignments/{brief['id']}/council"
    # Without consent no cloud model may be seated, and the refusal says who is missing.
    refused = client.post(f"{url}/guide", json={"allow_cloud": False})
    assert refused.status_code == 409
    assert "at least two models" in refused.json()["detail"]
    assert "Local model: Not running." in refused.json()["detail"]

    one = client.post(f"{url}/guide", json={"allow_cloud": True, "members": ["anthropic"]})
    assert one.status_code == 409 and "only Claude is ready" in one.json()["detail"]

    lecture = indexed_document["document"]["id"]
    assert client.post(f"/api/assignments/{lecture}/council/guide", json={}).status_code == 400
    assert client.post(f"{url}/essay", json={}).status_code == 404
    assert client.get("/api/assignments/nope/council").status_code == 404

    client.patch("/api/settings", json={"assignment_policy": "off"})
    off = client.post(f"{url}/guide", json={"allow_cloud": True})
    assert off.status_code == 409 and "switched off" in off.json()["detail"]


# --- folders -----------------------------------------------------------------------------
def test_folders_organise_a_course_and_its_assignments(
    client: TestClient, course: dict, indexed_document: dict
):
    url = f"/api/courses/{course['id']}/folders"
    assert client.get(url).json() == []
    made = client.post(url, json={"name": "  Week  1 "})
    assert made.status_code == 201 and made.json()[0]["name"] == "Week 1"
    week = made.json()[0]
    assert week["section"] == "course" and week["document_count"] == 0
    client.post(url, json={"name": "Coursework 1", "section": "assignments"})
    assert client.post(url, json={"name": "week 1"}).status_code == 409  # already there
    # The same name may be used once among lectures and once among assignments.
    assert client.post(url, json={"name": "Week 1", "section": "assignments"}).status_code == 201

    document_id = indexed_document["document"]["id"]
    filed = client.patch(f"/api/documents/{document_id}", json={"folder_id": week["id"]})
    assert filed.status_code == 200 and filed.json()["folder_id"] == week["id"]
    assert filed.json()["kind"] == "lecture"  # filing it does not change what it is
    counts = {f["name"] + f["section"]: f["document_count"] for f in client.get(url).json()}
    assert counts["Week 1course"] == 1

    renamed = client.patch(f"/api/folders/{week['id']}", json={"name": "Week one"})
    assert any(f["name"] == "Week one" for f in renamed.json())

    # A folder of another course is refused; an empty id takes the file out again.
    other = client.post("/api/courses", json={"title": "Other"}).json()
    elsewhere = client.post(f"/api/courses/{other['id']}/folders", json={"name": "X"}).json()[0]
    wrong = client.patch(f"/api/documents/{document_id}", json={"folder_id": elsewhere["id"]})
    assert wrong.status_code == 400
    assert (
        client.patch(f"/api/documents/{document_id}", json={"folder_id": ""}).json()["folder_id"]
        is None
    )

    # Deleting a folder leaves its files in the course.
    client.patch(f"/api/documents/{document_id}", json={"folder_id": week["id"]})
    left = client.delete(f"/api/folders/{week['id']}")
    assert left.status_code == 200 and all(f["id"] != week["id"] for f in left.json())
    assert client.get(f"/api/documents/{document_id}").json()["folder_id"] is None
    assert client.delete("/api/folders/nope").status_code == 404


def test_a_file_can_be_uploaded_straight_into_a_folder(
    client: TestClient, course: dict, sample_pdf_bytes: bytes
):
    folder = client.post(f"/api/courses/{course['id']}/folders", json={"name": "Tutorials"}).json()[
        0
    ]
    uploaded = client.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": ("t1.pdf", sample_pdf_bytes, "application/pdf")},
        data={"folder_id": folder["id"]},
    )
    assert uploaded.status_code == 201 and uploaded.json()["document"]["folder_id"] == folder["id"]
