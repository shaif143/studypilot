"""Running a discussion as a job, and what it needs from the database.

A discussion is fifteen to thirty model calls and takes minutes, so it runs in
a thread of its own, writes each draft, issue, ruling and vote to its row as
it happens, and stops when asked. The page that started it reads the row.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from sqlalchemy.orm import Session

from app.agents.service import build_retriever
from app.assignments.planner import (
    PER_REQUIREMENT_TOP_K,
    Requirement,
    _extract_requirements_heuristically,
)
from app.config import Settings
from app.council.engine import (
    Discussion,
    DiscussionStoppedError,
    Member,
    NoAnswerError,
    Task,
)
from app.models.base import ConsentRequiredError, ProviderUnavailableError
from app.models.registry import get_llm_provider
from app.runtime import RuntimeContext
from app.storage.models import CouncilRun, UserSettings
from app.storage.repositories import (
    CouncilRepository,
    CourseRepository,
    DocumentRepository,
    UserRepository,
)

log = logging.getLogger(__name__)

#: The four seats, in the order they sit. The order breaks a tied vote for who
#: writes the agreed version, so the stronger models come first.
SEATS: tuple[tuple[str, str], ...] = (
    ("anthropic", "Claude"),
    ("openai", "ChatGPT"),
    ("gemini", "Gemini"),
    ("ollama", "Local model"),
)
MAX_PASSAGES = 28
MAX_PASSAGE_CHARS = 2400


def describe_seats(
    settings: Settings, user_settings: UserSettings, *, allow_cloud: bool
) -> list[dict[str, Any]]:
    """The four seats and whether each can be filled right now, and if not, why."""
    consent = bool(user_settings.allow_cloud_processing and allow_cloud)
    chosen = user_settings.preferred_llm_models or {}
    seats: list[dict[str, Any]] = []
    for key, name in SEATS:
        seat: dict[str, Any] = {
            "key": key,
            "name": name,
            "model": "",
            "ready": False,
            "local": key == "ollama",
            "detail": "",
            "provider": None,
        }
        try:
            provider = get_llm_provider(
                settings, cloud_consent=consent, requested=key, model=str(chosen.get(key, "") or "")
            )
            info = provider.info
            if info.key != key or not info.generative:
                seat["detail"] = "It is not available on this installation."
            else:
                seat.update(ready=True, model=info.model, detail=info.detail, provider=provider)
        except ConsentRequiredError:
            seat["detail"] = (
                "It would send your material off this device. Allow cloud processing in "
                "Settings to seat it."
            )
        except ProviderUnavailableError as exc:
            seat["detail"] = str(exc)
        seats.append(seat)
    return seats


def members_from(seats: list[dict[str, Any]], only: list[str] | None = None) -> list[Member]:
    return [
        Member(
            key=seat["key"],
            name=seat["name"],
            provider=seat["provider"],
            model=seat["model"],
            local=seat["local"],
        )
        for seat in seats
        if seat["ready"] and (not only or seat["key"] in only)
    ]


def gather_passages(
    context: RuntimeContext,
    session: Session,
    *,
    user_id: str,
    course_id: str,
    brief_title: str,
    brief_text: str,
    document_ids: list[str],
) -> list[dict[str, Any]]:
    """The passages of the chosen references that bear on the brief, numbered.

    One retrieval per requirement of the brief, merged, so that every part of
    the assignment brings its own passages rather than the brief's opening
    lines deciding everything.
    """
    if not document_ids:
        return []
    _summary, requirements = _extract_requirements_heuristically(brief_text)
    queries = [Requirement(id="r0", text=brief_title, kind="task", quote=brief_title)]
    queries.extend(requirements[:14])
    retriever = build_retriever(context, session)
    pool: dict[str, Any] = {}
    for requirement in queries:
        if not requirement.text.strip():
            continue
        outcome = retriever.retrieve(
            requirement.text,
            user_id=user_id,
            course_ids=[course_id],
            document_ids=document_ids,
            top_k=PER_REQUIREMENT_TOP_K,
        )
        for chunk in outcome.chunks:
            if chunk.chunk_id not in pool or chunk.score > pool[chunk.chunk_id].score:
                pool[chunk.chunk_id] = chunk
    ranked = sorted(pool.values(), key=lambda chunk: chunk.score, reverse=True)[:MAX_PASSAGES]
    return [
        {
            "marker": marker,
            "chunk_id": chunk.chunk_id,
            "document_id": chunk.document_id,
            "document_title": chunk.document_title,
            "page_number": chunk.page_number,
            "section": chunk.section,
            "bbox": chunk.bbox,
            "score": float(chunk.score),
            "text": (chunk.parent_text or chunk.text)[:MAX_PASSAGE_CHARS],
        }
        for marker, chunk in enumerate(ranked, start=1)
    ]


def citations_from(passages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The passages as citations for the reader: where each is, and its opening words."""
    return [
        {
            "marker": passage["marker"],
            "chunk_id": passage["chunk_id"],
            "document_id": passage["document_id"],
            "document_title": passage["document_title"],
            "page_number": passage["page_number"],
            "section": passage["section"],
            "bbox": passage["bbox"],
            "score": passage["score"],
            "quote": " ".join(str(passage["text"]).split())[:240],
            "cited_inline": True,
        }
        for passage in passages
    ]


# --- the job -----------------------------------------------------------------------------
_stops: dict[str, threading.Event] = {}
_registry_lock = threading.Lock()


def is_running(run_id: str) -> bool:
    with _registry_lock:
        return run_id in _stops


def stop(run_id: str) -> bool:
    with _registry_lock:
        event = _stops.get(run_id)
    if event is None:
        return False
    event.set()
    return True


def start(
    context: RuntimeContext,
    *,
    run_id: str,
    user_id: str,
    allow_cloud: bool,
    member_keys: list[str] | None = None,
    wait: bool = False,
) -> None:
    event = threading.Event()
    with _registry_lock:
        _stops[run_id] = event
    kwargs = {
        "run_id": run_id,
        "user_id": user_id,
        "allow_cloud": allow_cloud,
        "member_keys": member_keys,
        "stop_event": event,
    }
    if wait:
        _run(context, **kwargs)
    else:
        threading.Thread(
            target=_run, args=(context,), kwargs=kwargs, name=f"council-{run_id[:8]}", daemon=True
        ).start()


def _run(
    context: RuntimeContext,
    *,
    run_id: str,
    user_id: str,
    allow_cloud: bool,
    member_keys: list[str] | None,
    stop_event: threading.Event,
) -> None:
    try:
        with context.database.session() as session:
            runs = CouncilRepository(session)
            run = runs.get(user_id, run_id)
            try:
                _discuss(context, session, run, user_id, allow_cloud, member_keys, stop_event)
            except DiscussionStoppedError:
                run.status = "cancelled"
                run.phase_detail = "Stopped. What had been said is kept below."
            except NoAnswerError as exc:
                run.status = "failed"
                run.error = str(exc)
            except Exception as exc:
                log.exception("council_failed", extra={"run_id": run_id})
                session.rollback()
                run = runs.get(user_id, run_id)
                run.status = "failed"
                run.error = str(exc)[:600]
            session.commit()
    finally:
        with _registry_lock:
            _stops.pop(run_id, None)


def _discuss(
    context: RuntimeContext,
    session: Session,
    run: CouncilRun,
    user_id: str,
    allow_cloud: bool,
    member_keys: list[str] | None,
    stop_event: threading.Event,
) -> None:
    documents = DocumentRepository(session)
    brief = documents.get(user_id, run.document_id)
    course = CourseRepository(session).get(user_id, run.course_id)
    user_settings = UserRepository(session).settings(user_id)
    seats = describe_seats(context.settings, user_settings, allow_cloud=allow_cloud)
    members = members_from(seats, member_keys)
    if not members:
        raise NoAnswerError("None of the models can be used right now.")

    brief_text = "\n\n".join(page.text for page in documents.pages(user_id, brief.id) if page.text)
    passages = gather_passages(
        context,
        session,
        user_id=user_id,
        course_id=run.course_id,
        brief_title=brief.title,
        brief_text=brief_text,
        document_ids=[str(item) for item in run.reference_document_ids],
    )
    guide = ""
    if run.kind == "solution":
        agreed = CouncilRepository(session).latest_done(user_id, run.document_id, "guide")
        guide = agreed.result_markdown if agreed else ""

    run.status = "running"
    run.members = [
        {"key": m.key, "name": m.name, "model": m.model, "local": m.local} for m in members
    ]
    run.citations = citations_from(passages)
    warnings: list[str] = []
    if not passages:
        warnings.append(
            "No reference passages were found in the files you chose, so the models worked "
            "from the brief and their own knowledge."
        )
    if len(members) == 1:
        warnings.append(
            f"Only {members[0].name} could be used, so nothing checked its answer. Set up a "
            "second model in Settings to have it discussed."
        )
    run.warnings = warnings
    session.commit()

    def emit(event: dict[str, Any]) -> None:
        if event.get("type") == "phase":
            run.phase = str(event.get("phase", ""))
            run.phase_detail = str(event.get("detail", ""))
        else:
            # Reassigned, not appended to: a JSON column only notices a new list.
            run.events = [*run.events, event]
        session.commit()

    task = Task(
        kind=run.kind,
        course_title=course.title,
        brief_title=brief.title,
        brief_text=brief_text,
        passages=passages,
        guide=guide,
    )
    outcome = Discussion(task, members, emit=emit, should_stop=stop_event.is_set).run()

    run.result_markdown = outcome.text
    run.consensus = outcome.consensus()
    run.events = [
        *run.events,
        {
            "type": "issues",
            "phase": "done",
            "issues": [issue.to_dict() for issue in outcome.issues],
        },
    ]
    run.phase = "done"
    run.phase_detail = ""
    run.status = "done"
