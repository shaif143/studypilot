"""Teaching slides for a page: read what is written, or have it written.

Also what belongs to a slide or a lecture beside its explanation: the class
transcript the explanation draws on, and the student's own note on a slide.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.agents.service import resolve_provider
from app.api.deps import (
    ContextDep,
    DocumentsDep,
    SessionDep,
    SlideGuidesDep,
    SlideNotesDep,
    TranscriptsDep,
    UserDep,
    UsersDep,
    require_token,
)
from app.api.schemas import (
    ExplainAllRequest,
    GuideCardOut,
    GuidedPagesOut,
    GuideJobOut,
    SlideGuideOut,
    SlideGuideRequest,
    SlideGuideStatusOut,
    SlideNoteIn,
    SlideNoteListItem,
    SlideNoteOut,
    TranscriptOut,
    TranscriptStatusOut,
    VideoLinkOut,
)
from app.learning import guide_service
from app.learning.slide_guide import BEYOND_THE_SLIDE, GuideDraft, video_links
from app.learning.transcripts import TranscriptError, read_transcript
from app.storage.models import LectureTranscript, SlideGuide

router = APIRouter(prefix="/api", tags=["guides"], dependencies=[Depends(require_token)])

#: A transcript is text; anything larger than this is not one.
MAX_TRANSCRIPT_BYTES = 12 * 1024 * 1024


def _cards_out(cards: list[dict[str, str]]) -> list[GuideCardOut]:
    return [
        GuideCardOut(
            kind=card.get("kind", "explanation"),
            title=card.get("title", ""),
            markdown=card.get("markdown", ""),
            beyond_slide=card.get("kind") in BEYOND_THE_SLIDE,
        )
        for card in cards
    ]


def _stored_out(row: SlideGuide) -> SlideGuideOut:
    return SlideGuideOut(
        document_id=row.document_id,
        page_number=row.page_number,
        cards=_cards_out(list(row.cards or [])),
        videos=[VideoLinkOut(**link) for link in video_links(list(row.video_queries or []))],
        generated_by=row.generated_by,
        model=row.model,
        level=row.level,
        saw_image=row.saw_image,
        used_transcript=bool(row.used_transcript),
        saved=True,
        updated_at=row.updated_at,
    )


def _draft_out(document_id: str, page_number: int, level: str, draft: GuideDraft) -> SlideGuideOut:
    return SlideGuideOut(
        document_id=document_id,
        page_number=page_number,
        cards=_cards_out([card.to_dict() for card in draft.cards]),
        videos=[VideoLinkOut(**link) for link in video_links(draft.video_queries)],
        generated_by=draft.generated_by,
        model=draft.model,
        level=level,
        saw_image=draft.saw_image,
        used_transcript=draft.used_transcript,
        saved=False,
        warnings=draft.warnings,
    )


def _job_out(document_id: str) -> GuideJobOut | None:
    job = guide_service.job_for(document_id)
    return GuideJobOut(**job.to_dict()) if job is not None else None  # type: ignore[arg-type]


def _guided(
    user_id: str,
    document_id: str,
    documents: DocumentsDep,
    guides: SlideGuidesDep,
    transcripts: TranscriptsDep,
) -> GuidedPagesOut:
    document = documents.get(user_id, document_id)  # ownership
    return GuidedPagesOut(
        pages=guides.pages_with_guides(user_id, document_id),
        page_count=document.page_count,
        job=_job_out(document_id),
        has_transcript=transcripts.get(user_id, document_id) is not None,
    )


@router.get("/documents/{document_id}/guides", response_model=GuidedPagesOut)
def guided_pages(
    document_id: str,
    user: UserDep,
    documents: DocumentsDep,
    guides: SlideGuidesDep,
    transcripts: TranscriptsDep,
) -> GuidedPagesOut:
    """Which pages already have teaching slides, and how far an explain-all job has got."""
    return _guided(user.id, document_id, documents, guides, transcripts)


@router.post("/documents/{document_id}/guides/explain-all", response_model=GuidedPagesOut)
def explain_all(
    document_id: str,
    payload: ExplainAllRequest,
    context: ContextDep,
    user: UserDep,
    users: UsersDep,
    documents: DocumentsDep,
    guides: SlideGuidesDep,
    transcripts: TranscriptsDep,
    session: SessionDep,
) -> GuidedPagesOut:
    """Start explaining every slide of this lecture that is not explained yet.

    Returns at once; the job runs in the background and this document's
    ``/guides`` reports its progress. Refused up front without a model that
    is allowed to read the material, rather than failing slide by slide.
    """
    documents.get(user.id, document_id)  # ownership
    provider, warnings = resolve_provider(
        context.settings,
        users.settings(user.id),
        requested=None,
        allow_cloud_request=payload.allow_cloud,
    )
    if not provider.info.generative:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                warnings[0]
                if warnings
                else "Choose an AI model in Settings and allow it to read your material first."
            ),
        )
    session.commit()  # the job's own session must see what this one has done
    guide_service.start(
        context,
        user_id=user.id,
        document_id=document_id,
        allow_cloud=payload.allow_cloud,
        level=payload.level,
        regenerate=payload.regenerate,
    )
    return _guided(user.id, document_id, documents, guides, transcripts)


@router.delete("/documents/{document_id}/guides/explain-all", response_model=GuidedPagesOut)
def stop_explaining(
    document_id: str,
    user: UserDep,
    documents: DocumentsDep,
    guides: SlideGuidesDep,
    transcripts: TranscriptsDep,
) -> GuidedPagesOut:
    """Stop an explain-all job. Slides already written are kept."""
    documents.get(user.id, document_id)  # ownership
    guide_service.cancel(document_id)
    return _guided(user.id, document_id, documents, guides, transcripts)


@router.get(
    "/documents/{document_id}/pages/{page_number}/guide", response_model=SlideGuideStatusOut
)
def read_guide(
    document_id: str,
    page_number: int,
    user: UserDep,
    documents: DocumentsDep,
    guides: SlideGuidesDep,
) -> SlideGuideStatusOut:
    """The teaching slides kept for this page, if any. Never calls a model."""
    documents.page(user.id, document_id, page_number)  # ownership, and the page exists
    row = guides.get(user.id, document_id, page_number)
    return SlideGuideStatusOut(guide=_stored_out(row) if row else None)


@router.post("/documents/{document_id}/pages/{page_number}/guide", response_model=SlideGuideOut)
def write_page_guide(
    document_id: str,
    page_number: int,
    payload: SlideGuideRequest,
    context: ContextDep,
    user: UserDep,
    users: UsersDep,
    documents: DocumentsDep,
    guides: SlideGuidesDep,
    session: SessionDep,
) -> SlideGuideOut:
    """Write the teaching slides for this page, or return the ones already kept.

    A model's guide is kept and returned from then on. The slide's own text,
    which is all there is without a model, is not kept — so choosing a model
    later gets the slide explained the next time it is opened.
    """
    document = documents.get(user.id, document_id)
    documents.page(user.id, document_id, page_number)  # the page exists
    existing = guides.get(user.id, document_id, page_number)
    if existing is not None and not payload.regenerate:
        return _stored_out(existing)

    user_settings = users.settings(user.id)
    level = payload.level or user_settings.explanation_level or "intermediate"
    provider, provider_warnings = resolve_provider(
        context.settings, user_settings, requested=None, allow_cloud_request=payload.allow_cloud
    )

    slide_image: bytes | None = None
    if provider.info.generative and provider.info.accepts_images:
        slide_image = guide_service.Original(context, session, user.id, document).page_image(
            page_number
        )

    draft = guide_service.draft_slide(
        provider=provider,
        course_title=guide_service.course_title_of(session, user.id, document),
        document=document,
        inputs=guide_service.slide_inputs(session, user.id, document, page_number),
        level=level,
        slide_image=slide_image,
    )
    draft.warnings = provider_warnings + draft.warnings

    if not draft.written:
        return _draft_out(document_id, page_number, level, draft)

    row = guide_service.keep(session, user.id, document_id, page_number, level, draft)
    out = _stored_out(row)
    out.warnings = draft.warnings
    return out


# --- the class transcript ----------------------------------------------------------------
def _transcript_out(row: LectureTranscript) -> TranscriptOut:
    return TranscriptOut(
        document_id=row.document_id,
        filename=row.filename,
        word_count=row.word_count,
        updated_at=row.updated_at,
    )


@router.get("/documents/{document_id}/transcript", response_model=TranscriptStatusOut)
def read_transcript_status(
    document_id: str, user: UserDep, documents: DocumentsDep, transcripts: TranscriptsDep
) -> TranscriptStatusOut:
    """Whether this lecture has a class transcript. Never the transcript's text."""
    documents.get(user.id, document_id)  # ownership
    row = transcripts.get(user.id, document_id)
    return TranscriptStatusOut(transcript=_transcript_out(row) if row else None)


@router.put("/documents/{document_id}/transcript", response_model=TranscriptStatusOut)
async def upload_transcript(
    document_id: str,
    user: UserDep,
    documents: DocumentsDep,
    transcripts: TranscriptsDep,
    file: UploadFile = File(...),
) -> TranscriptStatusOut:
    """Attach what was said in class to this lecture, replacing any earlier transcript.

    It is used when a slide is explained from now on. Slides already explained
    keep their text until they are explained again.
    """
    documents.get(user.id, document_id)  # ownership
    data = await file.read()
    if len(data) > MAX_TRANSCRIPT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="That file is too large to be a transcript. Upload the text, not the recording.",
        )
    try:
        text = read_transcript(file.filename or "transcript.txt", data)
    except TranscriptError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    row = transcripts.save(
        user.id, document_id, filename=file.filename or "transcript.txt", text=text
    )
    return TranscriptStatusOut(transcript=_transcript_out(row))


@router.delete("/documents/{document_id}/transcript", response_model=TranscriptStatusOut)
def delete_transcript(
    document_id: str, user: UserDep, documents: DocumentsDep, transcripts: TranscriptsDep
) -> TranscriptStatusOut:
    documents.get(user.id, document_id)  # ownership
    transcripts.delete(user.id, document_id)
    return TranscriptStatusOut(transcript=None)


# --- the student's own note on a slide ---------------------------------------------------
@router.get("/slide-notes", response_model=list[SlideNoteListItem])
def list_slide_notes(
    user: UserDep,
    documents: DocumentsDep,
    notes: SlideNotesDep,
    course_id: str | None = None,
    document_id: str | None = None,
) -> list[SlideNoteListItem]:
    """Every note the student has written on a slide, in lecture then slide order."""
    owned = {document.id: document for document in documents.list(user.id, course_id)}
    return [
        SlideNoteListItem(
            document_id=row.document_id,
            document_title=owned[row.document_id].title,
            course_id=owned[row.document_id].course_id,
            page_number=row.page_number,
            text=row.text,
            updated_at=row.updated_at,
        )
        for row in notes.list(user.id, document_id)
        if row.document_id in owned
    ]


@router.get("/documents/{document_id}/pages/{page_number}/note", response_model=SlideNoteOut)
def read_slide_note(
    document_id: str,
    page_number: int,
    user: UserDep,
    documents: DocumentsDep,
    notes: SlideNotesDep,
) -> SlideNoteOut:
    documents.page(user.id, document_id, page_number)  # ownership, and the page exists
    row = notes.get(user.id, document_id, page_number)
    return SlideNoteOut(
        document_id=document_id,
        page_number=page_number,
        text=row.text if row else "",
        updated_at=row.updated_at if row else None,
    )


@router.put("/documents/{document_id}/pages/{page_number}/note", response_model=SlideNoteOut)
def write_slide_note(
    document_id: str,
    page_number: int,
    payload: SlideNoteIn,
    user: UserDep,
    documents: DocumentsDep,
    notes: SlideNotesDep,
) -> SlideNoteOut:
    """Keep the student's note on this slide. Saving it empty removes it."""
    documents.page(user.id, document_id, page_number)
    row = notes.save(user.id, document_id, page_number, payload.text)
    return SlideNoteOut(
        document_id=document_id,
        page_number=page_number,
        text=row.text if row else "",
        updated_at=row.updated_at if row else None,
    )
