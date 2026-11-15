"""Teaching slides for a page: read what is written, or have it written."""

from __future__ import annotations

from contextlib import suppress

from fastapi import APIRouter, Depends

from app.agents.service import resolve_provider
from app.api.deps import (
    ContextDep,
    CoursesDep,
    DocumentsDep,
    DriveDep,
    LocalFoldersDep,
    SlideGuidesDep,
    UserDep,
    UsersDep,
    require_token,
)
from app.api.schemas import (
    GuideCardOut,
    GuidedPagesOut,
    SlideGuideOut,
    SlideGuideRequest,
    SlideGuideStatusOut,
    VideoLinkOut,
)
from app.ingestion.originals import OriginalUnavailableError, read_original
from app.learning.slide_guide import (
    BEYOND_THE_SLIDE,
    GuideDraft,
    render_slide_image,
    video_links,
    write_guide,
)
from app.storage.files import BlobNotFoundError
from app.storage.models import SlideGuide
from app.storage.repositories import NotFoundError

router = APIRouter(prefix="/api", tags=["guides"], dependencies=[Depends(require_token)])


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
        saved=False,
        warnings=draft.warnings,
    )


@router.get("/documents/{document_id}/guides", response_model=GuidedPagesOut)
def guided_pages(
    document_id: str, user: UserDep, documents: DocumentsDep, guides: SlideGuidesDep
) -> GuidedPagesOut:
    """Which pages already have teaching slides, so the slide list can mark them."""
    documents.get(user.id, document_id)  # ownership
    return GuidedPagesOut(pages=guides.pages_with_guides(user.id, document_id))


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
    courses: CoursesDep,
    guides: SlideGuidesDep,
    drive: DriveDep,
    folders: LocalFoldersDep,
) -> SlideGuideOut:
    """Write the teaching slides for this page, or return the ones already kept.

    A model's guide is kept and returned from then on. The slide's own text,
    which is all there is without a model, is not kept — so choosing a model
    later gets the slide explained the next time it is opened.
    """
    document = documents.get(user.id, document_id)
    page = documents.page(user.id, document_id, page_number)
    existing = guides.get(user.id, document_id, page_number)
    if existing is not None and not payload.regenerate:
        return _stored_out(existing)

    user_settings = users.settings(user.id)
    level = payload.level or user_settings.explanation_level or "intermediate"
    provider, provider_warnings = resolve_provider(
        context.settings, user_settings, requested=None, allow_cloud_request=payload.allow_cloud
    )

    def heading_of(number: int) -> str:
        with suppress(NotFoundError):
            return documents.page(user.id, document_id, number).heading
        return ""

    slide_image: bytes | None = None
    if provider.info.generative and provider.info.accepts_images:
        slide_image = _slide_image(context, user.id, document, page, drive, folders)

    course_title = ""
    with suppress(NotFoundError):
        course_title = courses.get(user.id, document.course_id).title

    draft = write_guide(
        provider=provider,
        course_title=course_title,
        document_title=document.title,
        page_number=page_number,
        page_count=document.page_count,
        heading=page.heading,
        text=page.text,
        previous_heading=heading_of(page_number - 1) if page_number > 1 else "",
        next_heading=heading_of(page_number + 1) if page_number < document.page_count else "",
        level=level,
        slide_image=slide_image,
    )
    draft.warnings = provider_warnings + draft.warnings

    if not draft.written:
        return _draft_out(document_id, page_number, level, draft)

    row = guides.save(
        user.id,
        document_id,
        page_number,
        level=level,
        cards=[card.to_dict() for card in draft.cards],
        video_queries=draft.video_queries,
        generated_by=draft.generated_by,
        model=draft.model,
        saw_image=draft.saw_image,
    )
    out = _stored_out(row)
    out.warnings = draft.warnings
    return out


def _slide_image(context, user_id, document, page, drive, folders) -> bytes | None:  # type: ignore[no-untyped-def]
    """The slide as a PNG: drawn from the original, which is fetched if it lives elsewhere.

    A slide that cannot be fetched or drawn is explained from its text alone;
    that is a worse explanation, not a failure.
    """
    try:
        data, _filename = read_original(context, user_id, document, drive, folders)
    except (OriginalUnavailableError, BlobNotFoundError):
        return None
    return render_slide_image(data, page.page_number)
