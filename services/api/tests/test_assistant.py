"""The Claude chat: keys, projects, conversations, and the loop that runs a turn.

Nothing here reaches Anthropic. A scripted client stands in for the SDK's: it
records each request it is sent and plays back the reply the test wrote, so
the tests can check both what the chat does with a reply and exactly what it
sends back.
"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient

from app.assistant import keys, service, tools
from app.runtime import RuntimeContext
from app.storage.repositories import AssistantRepository, FolderRepository, UserRepository

KEY = "sk-ant-api03-" + "k" * 40 + "WXYZ"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=="
)


# --- a scripted stand-in for the SDK -----------------------------------------
class Block(SimpleNamespace):
    def to_dict(self, mode: str = "python") -> dict[str, Any]:
        return dict(self.__dict__)


def text(value: str) -> Block:
    return Block(type="text", text=value)


def thinking(signature: str = "sig-1") -> Block:
    return Block(type="thinking", thinking="", signature=signature)


def tool_use(name: str, input: Any, id: str = "toolu_1") -> Block:
    return Block(type="tool_use", id=id, name=name, input=input)


def reply(*blocks: Block, stop_reason: str = "end_turn", model: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        content=list(blocks),
        stop_reason=stop_reason,
        model=model,
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


class Stream:
    def __init__(self, message: SimpleNamespace, on_event=None) -> None:
        self.message = message
        self.on_event = on_event

    def __enter__(self) -> Stream:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def __iter__(self):
        for block in self.message.content:
            yield SimpleNamespace(type="content_block_start", content_block=block)
            if block.type == "text":
                for word in block.text.split(" "):
                    if self.on_event:
                        self.on_event()
                    yield SimpleNamespace(
                        type="content_block_delta",
                        delta=SimpleNamespace(type="text_delta", text=word + " "),
                    )

    def get_final_message(self) -> SimpleNamespace:
        return self.message


class Scripted:
    """Plays replies back in order and keeps every request it was sent."""

    def __init__(self, *steps: Any) -> None:
        self.steps = list(steps)
        self.requests: list[dict[str, Any]] = []
        self.on_event = None
        self.key_checks = 0
        self.probe: Any = None
        self.beta = SimpleNamespace(
            messages=SimpleNamespace(stream=self._stream, create=self._create)
        )
        self.models = SimpleNamespace(list=self._list_models)

    def _stream(self, **request: Any) -> Stream:
        # A copy, as JSON: what was sent then, not what the list holds later.
        self.requests.append(json.loads(json.dumps(request)))
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return Stream(step, self.on_event)

    def _create(self, **request: Any) -> Any:
        self.requests.append(json.loads(json.dumps(request)))
        if isinstance(self.probe, Exception):
            raise self.probe
        return self.probe

    def _list_models(self, limit: int = 1) -> list[Any]:
        self.key_checks += 1
        return []


def api_error(cls: type, status: int, message: str) -> Exception:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message, response=httpx2.Response(status, request=request), body=None)


@pytest.fixture
def scripted(monkeypatch, settings) -> Scripted:
    # The operator's switch is on; each test turns the account's own on or off.
    settings.allow_cloud_processing = True
    client = Scripted()
    monkeypatch.setattr(service, "client_for", lambda _key: client)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return client


@pytest.fixture
def ready(client: TestClient, scripted: Scripted) -> TestClient:
    """A signed-in profile that has allowed cloud AI and saved a key."""
    client.patch("/api/settings", json={"allow_cloud_processing": True})
    saved = client.put("/api/assistant/key", json={"key": KEY})
    assert saved.status_code == 200, saved.text
    return client


def new_thread(client: TestClient, **body: Any) -> str:
    made = client.post("/api/assistant/threads", json=body)
    assert made.status_code == 201, made.text
    return made.json()["id"]


def send(client: TestClient, thread_id: str, **body: Any) -> list[dict[str, Any]]:
    body.setdefault("model", "claude-opus-5-5")
    response = client.post(f"/api/assistant/threads/{thread_id}/messages", json=body)
    assert response.status_code == 200, response.text
    return [json.loads(line) for line in response.text.splitlines() if line]


def kinds(events: list[dict[str, Any]]) -> list[str]:
    return [event["type"] for event in events]


# --- the key -------------------------------------------------------------------
def test_the_chat_is_not_ready_until_cloud_is_allowed_and_a_key_is_saved(
    client: TestClient, scripted: Scripted
):
    status = client.get("/api/assistant").json()
    assert status["ready"] is False
    assert status["key"] == {"configured": False, "source": "", "hint": ""}
    assert [model["id"] for model in status["models"]] == ["claude-fable-5-2", "claude-opus-5-5"]

    client.put("/api/assistant/key", json={"key": KEY})
    assert client.get("/api/assistant").json()["ready"] is False  # cloud still off

    client.patch("/api/settings", json={"allow_cloud_processing": True})
    status = client.get("/api/assistant").json()
    assert status["ready"] is True
    assert status["key"] == {"configured": True, "source": "account", "hint": "WXYZ"}


def test_a_saved_key_is_encrypted_and_never_returned(
    ready: TestClient, context: RuntimeContext, scripted: Scripted
):
    assert scripted.key_checks == 1  # Anthropic was asked before it was kept
    with context.database.session() as session:
        user = UserRepository(session).ensure_local_profile()
        row = AssistantRepository(session).key(user.id)
        assert row is not None
        assert KEY not in row.ciphertext and "sk-ant" not in row.ciphertext
        assert keys.resolve(AssistantRepository(session), context.settings, user.id).value == KEY
    assert KEY not in ready.get("/api/assistant").text

    cleared = ready.delete("/api/assistant/key").json()
    assert cleared["key"]["configured"] is False and cleared["ready"] is False


def test_a_key_anthropic_rejects_is_not_saved(client: TestClient, scripted: Scripted):
    def refuse(limit: int = 1):
        raise api_error(anthropic.AuthenticationError, 401, "invalid x-api-key")

    scripted.models = SimpleNamespace(list=refuse)
    response = client.put("/api/assistant/key", json={"key": KEY})
    assert response.status_code == 400
    assert "did not accept" in response.json()["detail"]
    assert client.get("/api/assistant").json()["key"]["configured"] is False


@pytest.mark.parametrize("value", ["hello", "sk-ant- with space", "AIzaSyNotAClaudeKey"])
def test_something_that_is_not_a_claude_key_is_refused_without_asking_anthropic(
    client: TestClient, scripted: Scripted, value: str
):
    assert client.put("/api/assistant/key", json={"key": value}).status_code == 400
    assert scripted.key_checks == 0


def test_a_single_user_installation_falls_back_to_its_own_key(
    client: TestClient, scripted: Scripted, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-the-environment-9999")
    key = client.get("/api/assistant").json()["key"]
    assert key == {"configured": True, "source": "installation", "hint": "9999"}


def test_a_hosted_site_never_lends_its_own_key(hosted_context: RuntimeContext, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-the-operators-key-0000")
    with hosted_context.database.session() as session:
        user = UserRepository(session).ensure_local_profile()
        repo = AssistantRepository(session)
        assert keys.resolve(repo, hosted_context.settings, user.id) is None
        keys.save(repo, hosted_context.settings, user.id, KEY)
        resolved = keys.resolve(repo, hosted_context.settings, user.id)
        assert resolved.value == KEY and resolved.source == "account"


def test_sending_needs_both_switches_and_a_key(client: TestClient, scripted: Scripted, settings):
    thread_id = new_thread(client)
    url = f"/api/assistant/threads/{thread_id}/messages"
    off = client.post(url, json={"text": "hi"})
    assert off.status_code == 403 and "Settings" in off.json()["detail"]

    client.patch("/api/settings", json={"allow_cloud_processing": True})
    settings.allow_cloud_processing = False
    operator = client.post(url, json={"text": "hi"})
    assert operator.status_code == 403 and "operator" in operator.json()["detail"]
    assert client.get("/api/assistant").json()["installation_allows_cloud"] is False

    settings.allow_cloud_processing = True
    no_key = client.post(url, json={"text": "hi"})
    assert no_key.status_code == 409 and "key" in no_key.json()["detail"]
    assert scripted.requests == []


# --- a turn ----------------------------------------------------------------------
def test_a_reply_is_streamed_and_kept(ready: TestClient, scripted: Scripted):
    scripted.steps = [reply(thinking(), text("Hello there"))]
    thread_id = new_thread(ready)
    events = send(ready, thread_id, text="Say hello", model="claude-fable-5-2")

    assert kinds(events) == ["thinking", "text", "text", "done"]
    assert "".join(e["text"] for e in events if e["type"] == "text") == "Hello there "
    thread = events[-1]["thread"]
    assert thread["title"] == "Say hello"
    assert thread["model"] == "claude-fable-5-2"
    assert [item["role"] for item in thread["items"]] == ["user", "assistant"]
    assert thread["items"][1]["blocks"] == [{"type": "text", "text": "Hello there"}]
    assert thread["usage"]["input"] == 100 and thread["usage"]["cost_usd"] > 0

    request = scripted.requests[0]
    assert request["model"] == "claude-fable-5-2"
    assert request["system"] == service.SYSTEM
    assert request["fallbacks"] == "default"
    assert request["betas"] == ["server-side-fallback-2026-07-01"]
    assert request["thinking"]["type"] == "adaptive"
    assert request["output_config"] == {"effort": "medium"}
    assert [tool["name"] for tool in request["tools"]][:2] == ["list_courses", "create_course"]
    assert all(tool["eager_input_streaming"] is True for tool in request["tools"])
    assert request["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "Say hello"}]}
    ]
    # Read back, it is the same conversation.
    assert ready.get(f"/api/assistant/threads/{thread_id}").json()["items"] == thread["items"]


def test_a_tool_call_acts_and_its_result_goes_back(
    ready: TestClient, scripted: Scripted, course: dict, context: RuntimeContext
):
    scripted.steps = [
        reply(
            thinking("sig-a"),
            text("Making it."),
            tool_use("create_folder", {"course_id": course["id"], "name": "Week 3"}),
            stop_reason="tool_use",
        ),
        reply(text("Done.")),
    ]
    thread_id = new_thread(ready)
    events = send(ready, thread_id, text="Make a Week 3 folder in Deep Learning")

    assert kinds(events) == [
        "thinking", "text", "text", "tool_pending", "tool_start", "tool_end", "text", "done",
    ]  # fmt: skip
    ended = next(e for e in events if e["type"] == "tool_end")
    assert ended["is_error"] is False
    assert ended["result"].startswith('Created the folder "Week 3"')
    with context.database.session() as session:
        user = UserRepository(session).ensure_local_profile()
        folders = FolderRepository(session).list(user.id, course["id"])
        assert [(f.name, f.section) for f in folders] == [("Week 3", "course")]

    # The second request carries the first reply exactly as it came, thinking
    # and its signature included, and then every result in one user message.
    second = scripted.requests[1]["messages"]
    assert second[1] == {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig-a"},
            {"type": "text", "text": "Making it."},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "create_folder",
                "input": {"course_id": course["id"], "name": "Week 3"},
            },
        ],
    }
    assert second[2]["role"] == "user"
    assert [block["type"] for block in second[2]["content"]] == ["tool_result"]
    assert second[2]["content"][0]["tool_use_id"] == "toolu_1"

    blocks = events[-1]["thread"]["items"][1]["blocks"]
    assert [block["type"] for block in blocks] == ["text", "tool", "text"]
    assert blocks[1]["done"] is True and blocks[1]["name"] == "create_folder"


def test_a_malformed_tool_input_is_answered_with_an_error_and_nothing_is_done(
    ready: TestClient, scripted: Scripted, course: dict, context: RuntimeContext
):
    scripted.steps = [
        reply(tool_use("create_folder", {"course_id": course["id"]}), stop_reason="tool_use"),
        reply(text("I could not.")),
    ]
    events = send(ready, new_thread(ready), text="Make a folder")
    ended = next(e for e in events if e["type"] == "tool_end")
    assert ended["is_error"] is True and "missing 'name'" in ended["result"]
    assert scripted.requests[1]["messages"][2]["content"][0]["is_error"] is True
    with context.database.session() as session:
        user = UserRepository(session).ensure_local_profile()
        assert FolderRepository(session).list(user.id, course["id"]) == []


def test_a_reply_cut_off_mid_call_does_not_run_the_call(
    ready: TestClient, scripted: Scripted, course: dict, context: RuntimeContext
):
    scripted.steps = [
        reply(
            tool_use("create_folder", {"course_id": course["id"], "name": "Half"}),
            stop_reason="max_tokens",
        ),
        reply(text("Trying again later.")),
    ]
    events = send(ready, new_thread(ready), text="Make a folder")
    ended = next(e for e in events if e["type"] == "tool_end")
    assert ended["is_error"] is True and "cut off" in ended["result"]
    with context.database.session() as session:
        user = UserRepository(session).ensure_local_profile()
        assert FolderRepository(session).list(user.id, course["id"]) == []


def test_a_declined_request_is_dropped_from_the_conversation(ready: TestClient, scripted: Scripted):
    scripted.steps = [reply(text("partial"), stop_reason="refusal"), reply(text("Sure."))]
    thread_id = new_thread(ready)
    events = send(ready, thread_id, text="Something declined")
    assert "reset" in kinds(events)
    error = next(e for e in events if e["type"] == "error")
    assert "declined" in error["message"]
    assert events[-1]["thread"]["items"] == []

    send(ready, thread_id, text="Something fine")
    assert scripted.requests[1]["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "Something fine"}]}
    ]


def test_a_conversation_whose_opening_changed_is_resent_without_thinking(
    ready: TestClient, scripted: Scripted
):
    thread_id = new_thread(ready)
    scripted.steps = [reply(thinking("sig-old"), text("First."))]
    send(ready, thread_id, text="One")

    scripted.steps = [
        api_error(
            anthropic.BadRequestError,
            400,
            "messages.1.content.0: Invalid `signature` in `thinking` block. The block is "
            "bound to a different conversation.",
        ),
        reply(text("Second.")),
    ]
    events = send(ready, thread_id, text="Two")
    assert "error" not in kinds(events)
    first_try, second_try = scripted.requests[1]["messages"], scripted.requests[2]["messages"]
    assert first_try[1]["content"][0]["type"] == "thinking"
    assert [block["type"] for block in second_try[1]["content"]] == ["text"]
    assert len(events[-1]["thread"]["items"]) == 4


@pytest.mark.parametrize(
    ("error", "words"),
    [
        (api_error(anthropic.AuthenticationError, 401, "invalid x-api-key"), "rejected"),
        (api_error(anthropic.RateLimitError, 429, "rate limited"), "limiting"),
        (api_error(anthropic.PermissionDeniedError, 403, "no access"), "cannot use"),
        (api_error(anthropic.InternalServerError, 529, "overloaded"), "having trouble"),
        (api_error(anthropic.BadRequestError, 400, "credit balance is too low"), "credit balance"),
    ],
)
def test_a_failed_request_is_explained_and_the_message_is_kept(
    ready: TestClient, scripted: Scripted, error: Exception, words: str
):
    scripted.steps = [error]
    events = send(ready, new_thread(ready), text="Hello")
    assert kinds(events) == ["error", "done"]
    assert words in events[0]["message"]
    assert KEY not in json.dumps(events)
    assert [item["role"] for item in events[-1]["thread"]["items"]] == ["user"]


def test_stopping_ends_the_reply_and_keeps_what_was_written(ready: TestClient, scripted: Scripted):
    thread_id = new_thread(ready)
    scripted.steps = [reply(thinking(), text("one two three four five"))]
    seen: list[int] = []

    def stop_at_the_third_word() -> None:
        seen.append(1)
        if len(seen) == 3:
            service.stop(thread_id)

    scripted.on_event = stop_at_the_third_word
    events = send(ready, thread_id, text="Count")
    assert events[-2] == {"type": "notice", "text": "Stopped."}
    items = events[-1]["thread"]["items"]
    assert [item["role"] for item in items] == ["user", "assistant"]
    assert items[1]["blocks"] == [{"type": "text", "text": "one two"}]

    # The half-finished reply goes back as plain text: its thinking was never signed.
    scripted.on_event = None
    scripted.steps = [reply(text("six"))]
    send(ready, thread_id, text="Go on")
    assert scripted.requests[1]["messages"][1] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "one two"}],
    }
    assert service.is_running(thread_id) is False
    assert ready.post(f"/api/assistant/threads/{thread_id}/stop").status_code == 204


def test_one_reply_at_a_time_in_a_conversation(ready: TestClient, scripted: Scripted):
    thread_id = new_thread(ready)
    assert service.begin(thread_id) is not None
    try:
        busy = ready.post(f"/api/assistant/threads/{thread_id}/messages", json={"text": "hi"})
        assert busy.status_code == 409
    finally:
        service.end(thread_id)


def test_a_switch_to_a_fallback_model_is_said_and_echoed_safely(
    ready: TestClient, scripted: Scripted
):
    scripted.steps = [
        reply(
            thinking("sig-fable"),
            text("Starting"),
            Block(type="fallback"),
            text("and finishing."),
            model="claude-opus-5",
        )
    ]
    events = send(ready, new_thread(ready), text="Hello", model="claude-fable-5-2")
    notice = next(e for e in events if e["type"] == "notice")
    assert "claude-opus-5 answered" in notice["text"]

    assert service.echo_safe(
        [
            {"type": "thinking", "thinking": "", "signature": "a"},
            {"type": "tool_use", "id": "t", "name": "x", "input": {}},
            {"type": "text", "text": "kept"},
            {"type": "fallback"},
            {"type": "thinking", "thinking": "", "signature": "b"},
            {"type": "text", "text": "after"},
        ]
    ) == [
        {"type": "text", "text": "kept"},
        {"type": "fallback"},
        {"type": "thinking", "thinking": "", "signature": "b"},
        {"type": "text", "text": "after"},
    ]


def test_a_claim_nobody_started_is_given_up(monkeypatch):
    """A request whose sender left before the reply began must not lock the chat."""
    clock = [1000.0]
    monkeypatch.setattr(service.time, "monotonic", lambda: clock[0])
    first = service.begin("chat-1")
    assert first is not None and service.begin("chat-1") is None

    clock[0] += 31
    second = service.begin("chat-1")
    assert second is not None and first.is_set()  # the lost one is told to stop
    service.end("chat-1", first)  # ...and its late release frees nothing
    assert service.is_running("chat-1")

    service.mark_started("chat-1", second)
    clock[0] += 600
    assert service.begin("chat-1") is None  # a reply being written is left alone
    service.end("chat-1", second)
    assert not service.is_running("chat-1")


# --- attachments -------------------------------------------------------------------
def attach(client: TestClient, thread_id: str, name: str, data: bytes) -> dict:
    made = client.post(
        f"/api/assistant/threads/{thread_id}/attachments", files={"file": (name, data)}
    )
    assert made.status_code == 201, made.text
    return made.json()


def test_a_photo_is_sent_as_an_image_the_same_way_every_time(
    ready: TestClient, scripted: Scripted, course: dict
):
    thread_id = new_thread(ready)
    photo = attach(ready, thread_id, "board.png", PNG)
    assert photo["kind"] == "image" and photo["media_type"] == "image/png"

    scripted.steps = [
        reply(tool_use("list_courses", {}), stop_reason="tool_use"),
        reply(text("A whiteboard.")),
    ]
    events = send(ready, thread_id, text="What is this?", attachment_ids=[photo["id"]])
    first, second = (request["messages"][0]["content"] for request in scripted.requests)
    assert first == second  # byte for byte: the signed thinking depends on it
    assert first[0] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.standard_b64encode(PNG).decode("ascii"),
        },
    }
    assert photo["id"] in first[1]["text"]
    assert first[2] == {"type": "text", "text": "What is this?"}

    item = events[-1]["thread"]["items"][0]
    assert item["text"] == "What is this?"
    assert [a["filename"] for a in item["attachments"]] == ["board.png"]
    shown = ready.get(f"/api/assistant/attachments/{photo['id']}/content")
    assert shown.content == PNG and shown.headers["content-type"] == "image/png"

    # Part of the conversation now, so it cannot be pulled out from under it.
    assert ready.delete(f"/api/assistant/attachments/{photo['id']}").status_code == 409


def test_an_attached_pdf_can_be_filed_into_a_course(
    ready: TestClient, scripted: Scripted, course: dict, sample_pdf_bytes: bytes
):
    thread_id = new_thread(ready)
    pdf = attach(ready, thread_id, "lecture.pdf", sample_pdf_bytes)
    assert pdf["kind"] == "pdf"
    scripted.steps = [
        reply(
            tool_use("save_attachment", {"attachment_id": pdf["id"], "course_id": course["id"]}),
            stop_reason="tool_use",
        ),
        reply(text("Added.")),
    ]
    events = send(ready, thread_id, text="Add this to Deep Learning", attachment_ids=[pdf["id"]])
    ended = next(e for e in events if e["type"] == "tool_end")
    assert ended["is_error"] is False, ended["result"]
    assert ended["result"].startswith('Added "lecture" to "Deep Learning"')
    documents = ready.get("/api/documents", params={"course_id": course["id"]}).json()
    assert [(d["title"], d["status"]) for d in documents] == [("lecture", "indexed")]
    assert scripted.requests[0]["messages"][0]["content"][0]["type"] == "document"


def test_what_a_file_is_decides_how_it_is_shown(settings):
    notes = service.prepare_file("notes.md", b"# Heading\n\nSome text.\n", settings)
    assert notes.kind == "text"
    deck = service.prepare_file("deck.pptx", b"PK\x03\x04" + b"\x00" * 64, settings)
    assert deck.kind == "file"
    with pytest.raises(ValueError, match="HEIC"):
        service.prepare_file("photo.heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32, settings)
    with pytest.raises(ValueError, match="empty"):
        service.prepare_file("nothing.txt", b"", settings)


# --- projects ------------------------------------------------------------------------
def test_a_project_gives_its_conversations_instructions_and_files(
    ready: TestClient, scripted: Scripted
):
    project = ready.post(
        "/api/assistant/projects",
        json={"name": "  Thesis  ", "instructions": "Always answer in British English."},
    ).json()
    assert project["name"] == "Thesis"
    added = ready.post(
        f"/api/assistant/projects/{project['id']}/files",
        files={"file": ("outline.md", b"# Outline\n1. Intro\n")},
    )
    assert added.status_code == 201, added.text

    thread_id = new_thread(ready, project_id=project["id"])
    scripted.steps = [reply(text("Noted."))]
    send(ready, thread_id, text="What is chapter one?")

    request = scripted.requests[0]
    assert request["system"].startswith(service.SYSTEM)
    assert "# Project: Thesis" in request["system"]
    assert "Always answer in British English." in request["system"]
    opening = request["messages"][0]["content"]
    assert "belong to the project" in opening[0]["text"]
    assert opening[1]["text"] == '<file name="outline.md">\n# Outline\n1. Intro\n\n</file>'
    assert opening[-1] == {"type": "text", "text": "What is chapter one?"}

    detail = ready.get(f"/api/assistant/projects/{project['id']}").json()
    assert detail["thread_count"] == 1 and detail["file_count"] == 1
    assert [thread["id"] for thread in detail["threads"]] == [thread_id]
    listed = ready.get("/api/assistant/projects").json()
    assert [(p["name"], p["thread_count"], p["file_count"]) for p in listed] == [("Thesis", 1, 1)]


def test_a_project_file_must_be_something_claude_can_read(ready: TestClient):
    project = ready.post("/api/assistant/projects", json={"name": "P"}).json()
    refused = ready.post(
        f"/api/assistant/projects/{project['id']}/files",
        files={"file": ("deck.pptx", b"PK\x03\x04" + b"\x00" * 64)},
    )
    assert refused.status_code == 400 and "course" in refused.json()["detail"]


def test_deleting_a_project_keeps_its_conversations(ready: TestClient, context: RuntimeContext):
    project = ready.post("/api/assistant/projects", json={"name": "Old"}).json()
    file = ready.post(
        f"/api/assistant/projects/{project['id']}/files", files={"file": ("a.txt", b"hello\n")}
    ).json()
    thread_id = new_thread(ready, project_id=project["id"])

    assert ready.delete(f"/api/assistant/projects/{project['id']}").status_code == 204
    assert ready.get("/api/assistant/projects").json() == []
    assert ready.get(f"/api/assistant/threads/{thread_id}").json()["project_id"] is None
    assert ready.get(f"/api/assistant/attachments/{file['id']}/content").status_code == 404
    stored = list((context.settings.preview_dir / "assistant").rglob(file["id"]))
    assert stored == []


def test_a_conversation_can_be_renamed_moved_and_deleted(ready: TestClient, scripted: Scripted):
    project = ready.post("/api/assistant/projects", json={"name": "Home"}).json()
    thread_id = new_thread(ready)
    url = f"/api/assistant/threads/{thread_id}"
    moved = ready.patch(url, json={"title": "  Revision  plan ", "project_id": project["id"]})
    assert moved.json()["title"] == "Revision plan"
    assert moved.json()["project_id"] == project["id"]
    assert ready.patch(url, json={"project_id": ""}).json()["project_id"] is None

    in_project = ready.get("/api/assistant/threads", params={"project_id": project["id"]})
    assert in_project.json() == []
    # Opened but never written in: not listed until something is said.
    assert ready.get("/api/assistant/threads").json() == []
    scripted.steps = [reply(text("Hello."))]
    send(ready, thread_id, text="Hi")
    assert [t["id"] for t in ready.get("/api/assistant/threads").json()] == [thread_id]
    assert ready.delete(url).status_code == 204
    assert ready.get(url).status_code == 404


def test_another_accounts_chat_cannot_be_reached(context: RuntimeContext):
    with context.database.session() as session:
        users = UserRepository(session)
        mine = users.ensure_local_profile()
        theirs = users.ensure_local_profile(handle="someone-else")
        repo = AssistantRepository(session)
        thread = repo.create_thread(theirs.id)
        project = repo.create_project(theirs.id, name="Private")
        from app.storage.repositories import NotFoundError

        with pytest.raises(NotFoundError):
            repo.thread(mine.id, thread.id)
        with pytest.raises(NotFoundError):
            repo.project(mine.id, project.id)
        assert repo.threads(mine.id) == []


# --- the tools ------------------------------------------------------------------------
def test_tools_tidy_a_course_and_read_it(
    client: TestClient, indexed_document: dict, context: RuntimeContext
):
    document = indexed_document["document"]
    with context.database.session() as session:
        user = UserRepository(session).ensure_local_profile()
        box = tools.Toolbox(context, session, user)
        course_id = document["course_id"]

        listed = box.run("list_courses", {})
        assert listed.text.startswith("Found 1 course.") and course_id in listed.text

        made = box.run("create_folder", {"course_id": course_id, "name": "Week 1"})
        folder_id = json.loads(made.text.split("\n", 1)[1])["folder_id"]
        again = box.run("create_folder", {"course_id": course_id, "name": "week 1"})
        assert "already has a folder" in again.text

        moved = box.run("move_file", {"document_id": document["id"], "folder_id": folder_id})
        assert moved.text == f'Moved "{document["title"]}" into the folder "Week 1".'
        files = json.loads(box.run("list_files", {"course_id": course_id}).text.split("\n", 1)[1])
        assert files["files"][0]["folder"] == "Week 1"

        renamed = box.run("rename_folder", {"folder_id": folder_id, "name": "Week One"})
        assert renamed.text == 'Renamed the folder "Week 1" to "Week One".'
        removed = box.run("delete_folder", {"folder_id": folder_id})
        assert "still in the course" in removed.text
        files = json.loads(box.run("list_files", {"course_id": course_id}).text.split("\n", 1)[1])
        assert files["folders"] == [] and files["files"][0]["folder"] == ""

        read = box.run("read_file", {"document_id": document["id"], "first_page": 1})
        assert read.text.startswith("Read pages 1 to") and "--- page 1 ---" in read.text
        found = box.run("search_materials", {"query": "convolution"})
        assert found.is_error is False

        note = box.run(
            "write_note",
            {"course_id": course_id, "title": "Summary", "body_markdown": "Key points."},
        )
        assert note.text.startswith('Saved the note "Summary"')

        missing = box.run("list_files", {"course_id": "nope"})
        assert missing.is_error and "No course with that id" in missing.text
        unknown = box.run("delete_everything", {})
        assert unknown.is_error and "no tool called" in unknown.text
    assert [n["title"] for n in client.get("/api/notes").json()] == ["Summary"]


def test_no_tool_deletes_a_file_or_a_course():
    names = {tool["name"] for tool in tools.TOOLS}
    assert not {name for name in names if "delete" in name or "remove" in name} - {"delete_folder"}


# --- connectors ---------------------------------------------------------------------
def add_connector(client: TestClient, **body: Any) -> dict:
    body.setdefault("label", "Deep Wiki")
    body.setdefault("url", "https://mcp.example.com/mcp")
    made = client.post("/api/assistant/connectors", json=body)
    assert made.status_code == 201, made.text
    return made.json()


def mcp_use(tool: str = "ask", server: str = "deep-wiki", id: str = "mcptoolu_1") -> Block:
    return Block(type="mcp_tool_use", id=id, name=tool, server_name=server, input={"q": "x"})


def mcp_result(text_: str = "An answer.", id: str = "mcptoolu_1", is_error: bool = False) -> Block:
    return Block(
        type="mcp_tool_result",
        tool_use_id=id,
        is_error=is_error,
        content=[{"type": "text", "text": text_}],
    )


def mcp_listing(server: str = "deep-wiki") -> Block:
    return Block(type="mcp_tool_listing", mcp_server_name=server, tools=[{"name": "ask"}])


def test_a_connector_is_sent_with_its_token_and_the_token_is_never_returned(
    ready: TestClient, scripted: Scripted, context: RuntimeContext
):
    extensions = add_connector(ready, token="secret-token-123")
    connector = extensions["connectors"][0]
    assert connector["name"] == "deep-wiki" and connector["has_token"] is True
    assert "secret-token-123" not in json.dumps(extensions)
    with context.database.session() as session:
        user = UserRepository(session).ensure_local_profile()
        row = AssistantRepository(session).connectors(user.id)[0]
        assert "secret-token-123" not in row.token_ciphertext

    # A second with the same label gets a name of its own; a third is switched off.
    add_connector(ready)
    off = add_connector(ready, label="Other")["connectors"]
    other = next(row for row in off if row["label"] == "Other")
    ready.patch(f"/api/assistant/connectors/{other['id']}", json={"enabled": False})

    scripted.steps = [reply(text("Hello."))]
    send(ready, new_thread(ready), text="Hi")
    request = scripted.requests[-1]
    assert request["mcp_servers"] == [
        {
            "type": "url",
            "url": "https://mcp.example.com/mcp",
            "name": "deep-wiki",
            "authorization_token": "secret-token-123",
        },
        {"type": "url", "url": "https://mcp.example.com/mcp", "name": "deep-wiki-2"},
    ]
    assert request["tools"][-2:] == [
        {"type": "mcp_toolset", "mcp_server_name": "deep-wiki"},
        {"type": "mcp_toolset", "mcp_server_name": "deep-wiki-2"},
    ]
    assert request["betas"] == ["server-side-fallback-2026-07-01", "mcp-client-2026-09-15"]


def test_without_connectors_nothing_about_them_is_sent(ready: TestClient, scripted: Scripted):
    scripted.steps = [reply(text("Hello."))]
    send(ready, new_thread(ready), text="Hi")
    request = scripted.requests[-1]
    assert "mcp_servers" not in request
    assert request["betas"] == ["server-side-fallback-2026-07-01"]
    assert all(tool.get("type") != "mcp_toolset" for tool in request["tools"])


@pytest.mark.parametrize(
    "url",
    [
        "http://mcp.example.com/mcp",
        "https://localhost/mcp",
        "https://printer.local/mcp",
        "https://user:pw@mcp.example.com/mcp",
        "ftp://mcp.example.com",
    ],
)
def test_a_connector_must_be_a_public_https_address(ready: TestClient, url: str):
    refused = ready.post("/api/assistant/connectors", json={"label": "X", "url": url})
    assert refused.status_code == 400, refused.text
    assert ready.get("/api/assistant/extensions").json()["connectors"] == []


def test_only_so_many_connectors_can_be_on_at_once(ready: TestClient):
    for number in range(service.MAX_CONNECTORS):
        add_connector(ready, label=f"C{number}")
    over = ready.post(
        "/api/assistant/connectors", json={"label": "One more", "url": "https://a.example.com/"}
    )
    assert over.status_code == 400 and "Switch one off" in over.json()["detail"]


def test_what_a_connector_did_is_shown_as_a_step(ready: TestClient, scripted: Scripted):
    add_connector(ready)
    scripted.steps = [
        reply(
            mcp_listing(),
            thinking("s1"),
            mcp_use(),
            mcp_result("Python SDK for MCP."),
            text("It is an SDK."),
        )
    ]
    events = send(ready, new_thread(ready), text="What is it?")
    started = next(e for e in events if e["type"] == "tool_start")
    ended = next(e for e in events if e["type"] == "tool_end")
    assert started["name"] == "mcp__deep-wiki__ask" and started["id"] == ended["id"]
    assert ended["result"] == "Python SDK for MCP." and ended["is_error"] is False
    blocks = events[-1]["thread"]["items"][1]["blocks"]
    assert blocks[0] == {
        "type": "tool",
        "id": "mcptoolu_1",
        "name": "mcp__deep-wiki__ask",
        "result": "Python SDK for MCP.",
        "is_error": False,
        "done": True,
    }
    assert blocks[1] == {"type": "text", "text": "It is an SDK."}


def test_a_chat_outlives_the_connector_it_used(ready: TestClient, scripted: Scripted):
    """The API refuses a history that names a connector it was not given."""
    connector = add_connector(ready)["connectors"][0]
    thread_id = new_thread(ready)
    scripted.steps = [
        reply(mcp_listing(), thinking("s1"), mcp_use(), mcp_result("Python SDK."), text("An SDK."))
    ]
    send(ready, thread_id, text="What is it?")

    # Still connected: the reply goes back exactly as it came.
    scripted.steps = [reply(text("Yes."))]
    send(ready, thread_id, text="Sure?")
    kept = [block["type"] for block in scripted.requests[-1]["messages"][1]["content"]]
    assert kept == ["mcp_tool_listing", "thinking", "mcp_tool_use", "mcp_tool_result", "text"]

    assert ready.delete(f"/api/assistant/connectors/{connector['id']}").status_code == 200
    scripted.steps = [reply(text("Still here."))]
    events = send(ready, thread_id, text="And now?")
    assert "error" not in kinds(events)
    request = scripted.requests[-1]
    assert "mcp_servers" not in request
    sent = request["messages"][1]["content"]
    assert [block["type"] for block in sent] == ["text", "text"]  # no thinking, no mcp blocks
    assert "deep-wiki" in sent[0]["text"] and "Python SDK." in sent[0]["text"]
    assert "disconnected" in sent[0]["text"]
    # ...and the screen still shows the step that was taken.
    first_reply = events[-1]["thread"]["items"][1]["blocks"]
    assert first_reply[0]["name"] == "mcp__deep-wiki__ask" and first_reply[0]["done"] is True


def test_a_reply_that_only_used_a_connector_is_not_an_empty_reply(
    ready: TestClient, scripted: Scripted
):
    add_connector(ready)
    scripted.steps = [
        reply(mcp_listing(), mcp_use(), mcp_result("x"), stop_reason="pause_turn"),
        reply(text("Finished.")),
    ]
    events = send(ready, new_thread(ready), text="Go")
    assert "error" not in kinds(events)
    assert len(scripted.requests) == 2


def test_a_connector_can_be_tried_before_a_chat_depends_on_it(
    ready: TestClient, scripted: Scripted
):
    connector = add_connector(ready, token="tok")["connectors"][0]
    url = f"/api/assistant/connectors/{connector['id']}/test"
    scripted.probe = SimpleNamespace(
        content=[
            Block(
                type="mcp_tool_listing",
                mcp_server_name="deep-wiki",
                tools=[{"name": "ask"}, {"name": "read"}],
            ),
            text("ready"),
        ]
    )
    assert ready.post(url).json() == {"ok": True, "tools": ["ask", "read"], "detail": ""}
    assert scripted.requests[-1]["mcp_servers"][0]["authorization_token"] == "tok"

    scripted.probe = anthropic.BadRequestError(
        "400",
        response=httpx2.Response(400, request=httpx2.Request("POST", "https://x")),
        body={"error": {"message": "MCP server returned 401 Unauthorized"}},
    )
    failed = ready.post(url).json()
    assert failed["ok"] is False and "401 Unauthorized" in failed["detail"]
    assert "tok" not in failed["detail"]


# --- plugins --------------------------------------------------------------------------
def plugin_zip(**extra: str) -> bytes:
    import io
    import zipfile

    files = {
        "kit/.claude-plugin/plugin.json": json.dumps(
            {"name": "Study Kit", "version": "1.2.0", "description": "Revision helpers."}
        ),
        "kit/skills/cornell-notes/SKILL.md": (
            "---\nname: cornell-notes\ndescription: >\n  Turn a topic into Cornell notes.\n"
            "  Use when asked for them.\n---\n# Cornell notes\nWrite Cues, Notes, Summary.\n"
        ),
        "kit/skills/cornell-notes/layout.md": "At most four cues.\n",
        "kit/skills/cornell-notes/scripts/render.py": "print('never run')\n",
        "kit/skills/cornell-notes/logo.png": "\x89PNG\x00\x00",
        "kit/commands/quiz-me.md": (
            "---\ndescription: Three quick questions\n---\nAsk three questions about: $ARGUMENTS\n"
        ),
        "kit/.mcp.json": json.dumps(
            {
                "mcpServers": {
                    "wiki": {"type": "http", "url": "https://mcp.example.com/mcp"},
                    "files": {"command": "npx", "args": ["server"]},
                }
            }
        ),
        "kit/agents/reviewer.md": "An agent.",
        "../outside.md": "not part of it",
    }
    files.update(extra)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def test_a_plugin_is_read_for_its_skills_commands_and_remote_connectors():
    from app.assistant import plugins

    parsed = plugins.parse("kit.zip", plugin_zip())
    assert (parsed.name, parsed.version) == ("Study Kit", "1.2.0")
    skill = parsed.skills[0]
    assert skill["name"] == "cornell-notes"
    assert skill["description"] == "Turn a topic into Cornell notes. Use when asked for them."
    assert skill["body"].startswith("# Cornell notes")
    # Text only: the reference page and the script to read, never the picture.
    assert sorted(skill["files"]) == ["layout.md", "scripts/render.py"]
    assert parsed.commands == [
        {
            "name": "quiz-me",
            "description": "Three quick questions",
            "body": "Ask three questions about: $ARGUMENTS\n",
        }
    ]
    assert parsed.connectors == [{"label": "wiki", "url": "https://mcp.example.com/mcp"}]
    notes = " ".join(parsed.notes)
    assert "1 connector starts a program" in notes
    assert "agents are not used" in notes and "not run here" in notes


def test_a_skill_packed_on_its_own_is_a_plugin_of_one_skill():
    import io
    import zipfile

    from app.assistant import plugins

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("summarise/SKILL.md", "---\nname: Summarise It\n---\nBe brief.\n")
        archive.writestr("summarise/examples.md", "One example.\n")
    parsed = plugins.parse("summarise.skill", buffer.getvalue())
    assert parsed.name == "summarise"
    assert [(s["name"], sorted(s["files"])) for s in parsed.skills] == [
        ("summarise-it", ["examples.md"])
    ]


def test_what_is_not_a_plugin_is_refused_with_a_reason():
    import io
    import zipfile

    from app.assistant import plugins

    with pytest.raises(plugins.PluginError, match="not a plugin archive"):
        plugins.parse("notes.zip", b"just some text")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "hello")
    with pytest.raises(plugins.PluginError, match="Nothing usable"):
        plugins.parse("empty.zip", buffer.getvalue())
    with pytest.raises(plugins.PluginError, match="larger than"):
        plugins.parse("big.zip", b"0" * (plugins.MAX_ARCHIVE_BYTES + 1))


def install(client: TestClient, data: bytes | None = None) -> dict:
    made = client.post("/api/assistant/plugins", files={"file": ("kit.zip", data or plugin_zip())})
    assert made.status_code == 201, made.text
    return made.json()


def test_an_installed_skill_is_offered_loaded_and_its_files_read(
    ready: TestClient, scripted: Scripted
):
    extensions = install(ready)
    plugin = extensions["plugins"][0]
    assert [skill["name"] for skill in plugin["skills"]] == ["study-kit:cornell-notes"]
    assert plugin["skills"][0]["file_count"] == 2
    # Its remote connector arrives switched off; the local one is only mentioned.
    assert [(c["label"], c["enabled"]) for c in extensions["connectors"]] == [("wiki", False)]

    scripted.steps = [
        reply(tool_use("load_skill", {"name": "study-kit:cornell-notes"}), stop_reason="tool_use"),
        reply(
            tool_use("read_skill_file", {"name": "cornell-notes", "path": "layout.md"}, id="t2"),
            stop_reason="tool_use",
        ),
        reply(text("Here they are.")),
    ]
    events = send(ready, new_thread(ready), text="Cornell notes on overfitting")
    system = scripted.requests[0]["system"]
    assert "# Skills" in system
    assert "- study-kit:cornell-notes: Turn a topic into Cornell notes." in system
    assert "mcp_servers" not in scripted.requests[0]
    results = [e["result"] for e in events if e["type"] == "tool_end"]
    assert results[0].startswith('Loaded the skill "study-kit:cornell-notes".')
    assert "Write Cues, Notes, Summary." in results[0] and "- layout.md" in results[0]
    assert results[1].rstrip().endswith("At most four cues.")


def test_a_command_runs_its_instructions_and_shows_only_what_was_typed(
    ready: TestClient, scripted: Scripted
):
    extensions = install(ready)
    assert extensions["commands"] == [
        {"name": "study-kit:quiz-me", "description": "Three quick questions", "plugin": "Study Kit"}
    ]
    scripted.steps = [reply(text("1. ...")), reply(text("Not a command."))]
    thread_id = new_thread(ready)
    events = send(ready, thread_id, text="/quiz-me backpropagation")
    sent = scripted.requests[0]["messages"][0]["content"]
    assert "ran the command /study-kit:quiz-me" in sent[0]["text"]
    assert "Ask three questions about: backpropagation" in sent[0]["text"]
    assert sent[1] == {"type": "text", "text": "/quiz-me backpropagation"}
    assert events[-1]["thread"]["items"][0]["text"] == "/quiz-me backpropagation"

    # Something that only looks like a command is an ordinary message.
    send(ready, thread_id, text="/unknown thing")
    assert scripted.requests[1]["messages"][-1]["content"] == [
        {"type": "text", "text": "/unknown thing"}
    ]


def test_a_plugin_switched_off_or_removed_takes_its_parts_with_it(
    ready: TestClient, scripted: Scripted
):
    plugin = install(ready)["plugins"][0]
    off = ready.patch(f"/api/assistant/plugins/{plugin['id']}", json={"enabled": False}).json()
    assert off["commands"] == [] and off["plugins"][0]["enabled"] is False
    scripted.steps = [
        reply(tool_use("load_skill", {"name": "study-kit:cornell-notes"}), stop_reason="tool_use"),
        reply(text("No such skill.")),
    ]
    events = send(ready, new_thread(ready), text="/quiz-me x")
    assert "# Skills" not in scripted.requests[0]["system"]
    assert scripted.requests[0]["messages"][0]["content"] == [
        {"type": "text", "text": "/quiz-me x"}
    ]
    ended = next(e for e in events if e["type"] == "tool_end")
    assert ended["is_error"] is True and "none are installed" in ended["result"]

    gone = ready.delete(f"/api/assistant/plugins/{plugin['id']}").json()
    assert gone == {"connectors": [], "plugins": [], "commands": [], "max_connectors": 10}


def test_an_archive_that_is_not_a_plugin_is_refused_by_the_api(ready: TestClient):
    refused = ready.post("/api/assistant/plugins", files={"file": ("x.zip", b"not a zip")})
    assert refused.status_code == 400 and "not a plugin archive" in refused.json()["detail"]
