"""Writing teaching slides: for the slide that is open, or for a whole lecture.

One slide is written inside the request that asked for it. A whole lecture is
a job: fifty model calls take minutes, so it runs in a thread of its own,
reports how far it has got, and stops when asked. It writes a few slides at a
time — the calls are independent — and keeps each one as it arrives, so
stopping halfway loses nothing that was paid for.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.agents.service import resolve_provider
from app.ingestion.originals import OriginalUnavailableError, read_original
from app.learning.slide_guide import GuideDraft, render_slide_image, write_guide
from app.learning.transcripts import excerpts_for_slide
from app.runtime import RuntimeContext
from app.storage.files import BlobNotFoundError
from app.storage.models import Document, SlideGuide
from app.storage.repositories import (
    CourseRepository,
    DocumentRepository,
    DriveRepository,
    LocalFolderRepository,
    NotFoundError,
    SlideGuideRepository,
    TranscriptRepository,
    UserRepository,
)

log = logging.getLogger(__name__)

#: Slides written at once. Enough to turn half an hour into ten minutes
#: without tripping a provider's rate limit on a personal key.
PARALLEL_SLIDES = 3
#: Give up after this many slides in a row come back unwritten: the model is
#: unreachable or refusing, and the rest would fail the same way.
MAX_CONSECUTIVE_FAILURES = 3


class Original:
    """A lecture's file, fetched once and drawn page by page."""

    def __init__(self, context: RuntimeContext, session: Session, user_id: str, document: Document):
        self._args = (context, session, user_id, document)
        self._data: bytes | None = None
        self._tried = False

    def page_image(self, page_number: int) -> bytes | None:
        if not self._tried:
            self._tried = True
            context, session, user_id, document = self._args
            try:
                self._data, _name = read_original(
                    context,
                    user_id,
                    document,
                    DriveRepository(session),
                    LocalFolderRepository(session),
                )
            except (OriginalUnavailableError, BlobNotFoundError):
                # Explained from its text alone: a worse explanation, not a failure.
                self._data = None
        return render_slide_image(self._data, page_number) if self._data else None


@dataclass
class SlideInputs:
    """Everything a model call needs, read from the database beforehand."""

    page_number: int
    heading: str
    text: str
    previous_heading: str
    next_heading: str
    transcript_excerpts: list[str]


def slide_inputs(
    session: Session, user_id: str, document: Document, page_number: int
) -> SlideInputs:
    documents = DocumentRepository(session)
    page = documents.page(user_id, document.id, page_number)

    def heading_of(number: int) -> str:
        if not 1 <= number <= document.page_count:
            return ""
        with suppress(NotFoundError):
            return documents.page(user_id, document.id, number).heading
        return ""

    excerpts: list[str] = []
    transcript = TranscriptRepository(session).get(user_id, document.id)
    if transcript is not None and transcript.text:
        excerpts = excerpts_for_slide(
            transcript.text,
            slide_text=page.text,
            heading=page.heading,
            page_number=page_number,
            page_count=document.page_count,
        )
    return SlideInputs(
        page_number=page_number,
        heading=page.heading,
        text=page.text,
        previous_heading=heading_of(page_number - 1),
        next_heading=heading_of(page_number + 1),
        transcript_excerpts=excerpts,
    )


def course_title_of(session: Session, user_id: str, document: Document) -> str:
    with suppress(NotFoundError):
        return CourseRepository(session).get(user_id, document.course_id).title
    return ""


def draft_slide(
    *,
    provider: Any,
    course_title: str,
    document: Document,
    inputs: SlideInputs,
    level: str,
    slide_image: bytes | None,
) -> GuideDraft:
    """The model call. Touches no database, so it can run beside others."""
    return write_guide(
        provider=provider,
        course_title=course_title,
        document_title=document.title,
        page_number=inputs.page_number,
        page_count=document.page_count,
        heading=inputs.heading,
        text=inputs.text,
        previous_heading=inputs.previous_heading,
        next_heading=inputs.next_heading,
        level=level,
        slide_image=slide_image,
        transcript_excerpts=inputs.transcript_excerpts,
    )


def keep(
    session: Session,
    user_id: str,
    document_id: str,
    page_number: int,
    level: str,
    draft: GuideDraft,
) -> SlideGuide:
    return SlideGuideRepository(session).save(
        user_id,
        document_id,
        page_number,
        level=level,
        cards=[card.to_dict() for card in draft.cards],
        video_queries=draft.video_queries,
        generated_by=draft.generated_by,
        model=draft.model,
        saw_image=draft.saw_image,
        used_transcript=draft.used_transcript,
    )


# --- a whole lecture -------------------------------------------------------------------
@dataclass
class GuideJob:
    state: str = "queued"  # queued | running | done | failed | cancelled
    total: int = 0
    done: int = 0
    written: int = 0
    failed: int = 0
    error: str = ""
    finished_at: float = 0.0
    stop: threading.Event = field(default_factory=threading.Event)

    @property
    def active(self) -> bool:
        return self.state in {"queued", "running"}

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "total": self.total,
            "done": self.done,
            "written": self.written,
            "failed": self.failed,
            "error": self.error,
            "stopping": self.stop.is_set() and self.active,
        }


_jobs: dict[str, GuideJob] = {}
_registry_lock = threading.Lock()


def job_for(document_id: str) -> GuideJob | None:
    """The running or recently finished job for a lecture; finished ones linger ten minutes."""
    with _registry_lock:
        job = _jobs.get(document_id)
        if job and not job.active and time.time() - job.finished_at > 600:
            del _jobs[document_id]
            return None
        return job


def cancel(document_id: str) -> GuideJob | None:
    job = job_for(document_id)
    if job is not None and job.active:
        job.stop.set()
    return job


def start(
    context: RuntimeContext,
    *,
    user_id: str,
    document_id: str,
    allow_cloud: bool,
    level: str | None,
    regenerate: bool,
    wait_for_it: bool = False,
) -> GuideJob:
    """Explain every slide of a lecture that is not explained yet."""
    current = job_for(document_id)
    if current is not None and current.active:
        return current
    job = GuideJob()
    with _registry_lock:
        _jobs[document_id] = job
    kwargs = {
        "user_id": user_id,
        "document_id": document_id,
        "allow_cloud": allow_cloud,
        "level": level,
        "regenerate": regenerate,
    }
    if wait_for_it:
        _run(context, job, **kwargs)
    else:
        threading.Thread(
            target=_run,
            args=(context, job),
            kwargs=kwargs,
            name=f"explain-all-{document_id[:8]}",
            daemon=True,
        ).start()
    return job


def _run(
    context: RuntimeContext,
    job: GuideJob,
    *,
    user_id: str,
    document_id: str,
    allow_cloud: bool,
    level: str | None,
    regenerate: bool,
) -> None:
    try:
        with context.database.session() as session:
            document = DocumentRepository(session).get(user_id, document_id)
            user_settings = UserRepository(session).settings(user_id)
            depth = level or user_settings.explanation_level or "intermediate"
            provider, warnings = resolve_provider(
                context.settings, user_settings, requested=None, allow_cloud_request=allow_cloud
            )
            if not provider.info.generative:
                job.error = (
                    warnings[0]
                    if warnings
                    else "Choose an AI model in Settings and allow it to read your material first."
                )
                job.state = "failed"
                return

            guides = SlideGuideRepository(session)
            already = set(guides.pages_with_guides(user_id, document_id))
            pages = [
                number
                for number in range(1, document.page_count + 1)
                if regenerate or number not in already
            ]
            job.total = len(pages)
            job.state = "running"
            course_title = course_title_of(session, user_id, document)
            original = Original(context, session, user_id, document)
            wants_image = bool(provider.info.accepts_images)

            consecutive_failures = 0
            pending: dict[Future[GuideDraft], int] = {}
            queue = list(pages)

            def settle(future: Future[GuideDraft]) -> None:
                nonlocal consecutive_failures
                number = pending.pop(future)
                job.done += 1
                try:
                    draft = future.result()
                except Exception:  # one bad slide must not end the lecture
                    log.exception("explain_all_slide_failed", extra={"page": number})
                    job.failed += 1
                    consecutive_failures += 1
                    return
                if not draft.written:
                    job.failed += 1
                    consecutive_failures += 1
                    job.error = draft.warnings[0] if draft.warnings else job.error
                    return
                consecutive_failures = 0
                keep(session, user_id, document_id, number, depth, draft)
                session.commit()
                job.written += 1

            with ThreadPoolExecutor(
                max_workers=PARALLEL_SLIDES, thread_name_prefix="explain-slide"
            ) as pool:
                while queue or pending:
                    stopping = job.stop.is_set() or consecutive_failures >= MAX_CONSECUTIVE_FAILURES
                    while queue and not stopping and len(pending) < PARALLEL_SLIDES:
                        number = queue.pop(0)
                        if not regenerate and guides.get(user_id, document_id, number) is not None:
                            # Written meanwhile, by the student opening that slide.
                            job.done += 1
                            continue
                        inputs = slide_inputs(session, user_id, document, number)
                        image = original.page_image(number) if wants_image else None
                        future = pool.submit(
                            draft_slide,
                            provider=provider,
                            course_title=course_title,
                            document=document,
                            inputs=inputs,
                            level=depth,
                            slide_image=image,
                        )
                        pending[future] = number
                    if not pending:
                        break
                    finished, _ = wait(list(pending), return_when=FIRST_COMPLETED)
                    for future in finished:
                        settle(future)

            if job.stop.is_set():
                job.state = "cancelled"
            elif consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                job.state = "failed"
                job.error = job.error or "The AI model stopped answering."
            else:
                job.error = ""
                job.state = "done"
    except Exception as exc:
        log.exception("explain_all_failed", extra={"document_id": document_id})
        job.state = "failed"
        job.error = str(exc)
    finally:
        if job.active:
            job.state = "failed"
        job.finished_at = time.time()
