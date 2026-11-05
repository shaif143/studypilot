"""Document upload, reading and deletion.

Upload is synchronous on purpose for the first release: a student uploading a
lecture deck wants to know whether it worked, and a 25-slide PDF indexes in
well under a second with the local pipeline.  The response carries the real
ingestion outcome — page count, chunk count, and any extraction warnings — so
the UI never has to claim success it cannot verify.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response

from app.agents.service import resolve_provider
from app.api.deps import (
    ChunksDep,
    ContextDep,
    CoursesDep,
    DocumentsDep,
    DriveDep,
    LocalFoldersDep,
    SessionDep,
    TopicsDep,
    UserDep,
    UsersDep,
    require_token,
)
from app.api.schemas import (
    ArchiveMemberOut,
    ArchiveUploadResponse,
    DocumentOut,
    PageOut,
    PageSummaryOut,
    TopicOut,
    UploadResponse,
)
from app.ingestion.archives import UnreadableArchiveError, archive_kind, unpack
from app.ingestion.media import MediaReader, is_audio, is_image, normalise_image, sniff_media_type
from app.ingestion.pipeline import UnsupportedFileError, ingest_document, validate_upload
from app.ingestion.removal import remove_document
from app.storage.files import BlobNotFoundError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["documents"], dependencies=[Depends(require_token)])


@router.post(
    "/courses/{course_id}/documents",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    course_id: str,
    context: ContextDep,
    user: UserDep,
    courses: CoursesDep,
    documents: DocumentsDep,
    chunks: ChunksDep,
    topics: TopicsDep,
    users: UsersDep,
    file: UploadFile = File(...),
    title: str = Form(default=""),
    kind: str = Form(default="lecture"),
    #: Per-request consent to read an image or audio brief with a cloud model,
    #: required in addition to the stored setting. Ignored for PDFs, which are
    #: read on this machine without any model.
    allow_cloud: bool = Form(default=False),
) -> UploadResponse:
    courses.get(user.id, course_id)  # ownership check before touching the file
    data = await file.read()
    try:
        return _ingest_upload(
            filename=file.filename or "upload",
            data=data,
            title=title,
            kind=kind,
            allow_cloud=allow_cloud,
            course_id=course_id,
            context=context,
            user=user,
            documents=documents,
            chunks=chunks,
            topics=topics,
            users=users,
        )
    except UnsupportedFileError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/courses/{course_id}/documents/archive",
    response_model=ArchiveUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_archive(
    course_id: str,
    context: ContextDep,
    user: UserDep,
    courses: CoursesDep,
    documents: DocumentsDep,
    chunks: ChunksDep,
    topics: TopicsDep,
    users: UsersDep,
    file: UploadFile = File(...),
    kind: str = Form(default="lecture"),
    #: Consent for a cloud model to read the images and recordings inside.
    allow_cloud: bool = Form(default=False),
) -> ArchiveUploadResponse:
    """A .zip or .rar: every readable member becomes a document of its own.

    Each member's outcome is reported by its path inside the archive, and
    members the app cannot read are listed with a reason rather than lost.
    Images and recordings go to the same consent-gated model as a single
    upload; when none can read them, they are skipped with that reason, in
    one line, rather than becoming a row of failed documents. The archive
    itself is not kept.
    """
    courses.get(user.id, course_id)
    data = await file.read()
    settings = context.settings
    if len(data) > settings.max_upload_bytes:
        limit_mb = settings.max_upload_bytes // (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"That archive is larger than the {limit_mb} MB limit.",
        )
    archive_name = file.filename or "archive"
    archive = archive_kind(data, archive_name)
    if not archive:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That file is not a .zip or .rar archive.",
        )
    try:
        unpacked = unpack(
            data,
            archive,
            max_member_bytes=settings.max_upload_bytes,
            max_total_bytes=settings.max_upload_bytes * 4,
        )
    except UnreadableArchiveError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    del data

    results: list[ArchiveMemberOut] = []
    refusals: dict[str, str] = {}  # media type → why it cannot be read, decided once
    for member in unpacked.members:
        name, data = normalise_image(Path(member.path).name, member.data)
        media = sniff_media_type(data, name) or ""
        if is_image(media) or is_audio(media):
            if media not in refusals:
                reader = _media_reader_for(media, allow_cloud, context, user, users)
                refusals[media] = reader.cannot_read(media)
            if refusals[media]:
                results.append(
                    ArchiveMemberOut(path=member.path, status="skipped", detail=refusals[media])
                )
                continue
        try:
            result = _ingest_upload(
                filename=name,
                data=data,
                title=Path(member.path).stem,
                kind=kind,
                allow_cloud=allow_cloud,
                course_id=course_id,
                context=context,
                user=user,
                documents=documents,
                chunks=chunks,
                topics=topics,
                users=users,
            )
        except UnsupportedFileError as exc:
            results.append(ArchiveMemberOut(path=member.path, status="skipped", detail=str(exc)))
            continue
        results.append(ArchiveMemberOut(path=member.path, status=result.status, result=result))
    for skipped in unpacked.skipped:
        results.append(ArchiveMemberOut(path=skipped.path, status="skipped", detail=skipped.reason))
    return ArchiveUploadResponse(archive=archive_name, members=results)


def _media_reader_for(
    media_type: str, allow_cloud: bool, context: ContextDep, user: UserDep, users: UsersDep
) -> MediaReader:
    """The consent-gated model that reads an image or a recording.

    The same gate as the tutor: the stored preference and this request's
    flag both have to say yes before a cloud model sees the file.
    """
    settings = context.settings
    requested = settings.vision_provider or settings.assignment_provider or None
    if requested is None and settings.llm_provider == "extractive":
        from app.models.llm import AnthropicProvider, GeminiProvider

        # Audio needs a provider that accepts it; images prefer Anthropic.
        if is_audio(media_type) and GeminiProvider().info.available:
            requested = "gemini"
        elif AnthropicProvider().info.available:
            requested = "anthropic"
        elif GeminiProvider().info.available:
            requested = "gemini"
    provider, _warnings = resolve_provider(
        settings, users.settings(user.id), requested=requested, allow_cloud_request=allow_cloud
    )
    return MediaReader(settings=settings, provider=provider)


def _ingest_upload(
    *,
    filename: str,
    data: bytes,
    title: str,
    kind: str,
    allow_cloud: bool,
    course_id: str,
    context: ContextDep,
    user: UserDep,
    documents: DocumentsDep,
    chunks: ChunksDep,
    topics: TopicsDep,
    users: UsersDep,
) -> UploadResponse:
    """Validate, store, and index one file; shared by the upload and archive routes."""
    filename, data = normalise_image(filename, data)
    media_type = validate_upload(filename, data, context.settings)

    content_hash = context.blobs.hash_bytes(data)
    existing = documents.find_by_hash(user.id, course_id, content_hash)
    if existing is not None and existing.status not in {"indexed", "partial"}:
        # The same file failed before (no model for it, say): try again in
        # its place rather than leaving a second failed row behind.
        remove_document(
            context=context,
            session=documents.session,
            documents=documents,
            chunks=chunks,
            document=existing,
        )
        existing = None
    if existing is not None and existing.status in {"indexed", "partial"}:
        # Same bytes, same course: return the document already indexed rather
        # than silently creating a duplicate.
        return UploadResponse(
            document=DocumentOut.model_validate(existing),
            status=existing.status,
            page_count=existing.page_count,
            chunk_count=len(chunks.by_document(user.id, existing.id, level="child")),
            topic_count=0,
            warnings=["This file is already in this course; the existing copy was reused."],
            duration_ms=0,
            deduplicated=True,
        )

    context.blobs.put(data)
    if kind not in {"lecture", "past_paper", "assignment", "textbook"}:
        kind = "lecture"

    document = documents.create(
        user_id=user.id,
        course_id=course_id,
        title=(title.strip() or Path(filename).stem)[:512],
        original_filename=filename[:512],
        media_type=media_type,
        content_hash=content_hash,
        byte_size=len(data),
        kind=kind,
        status="pending",
    )

    media_reader = None
    if is_image(media_type) or is_audio(media_type):
        media_reader = _media_reader_for(media_type, allow_cloud, context, user, users)
    elif media_type == "application/pdf":
        # Only used if the PDF turns out to have no text layer (a scan); the
        # reader carries the consent decision, and declines without it.
        media_reader = _media_reader_for("image/png", allow_cloud, context, user, users)

    result = ingest_document(
        document=document,
        settings=context.settings,
        blobs=context.blobs,
        embedder=context.embedder,
        vector_store=context.vector_store,
        sparse_index=context.sparse_index,
        documents=documents,
        chunks=chunks,
        topics=topics,
        media_reader=media_reader,
    )

    return UploadResponse(
        document=DocumentOut.model_validate(document),
        status=result.status,
        page_count=result.page_count,
        chunk_count=result.chunk_count,
        topic_count=result.topic_count,
        warnings=result.warnings,
        duration_ms=result.duration_ms,
    )


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(
    user: UserDep, documents: DocumentsDep, course_id: str | None = None
) -> list[DocumentOut]:
    return [DocumentOut.model_validate(row) for row in documents.list(user.id, course_id)]


@router.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: str, user: UserDep, documents: DocumentsDep) -> DocumentOut:
    return DocumentOut.model_validate(documents.get(user.id, document_id))


@router.get("/documents/{document_id}/pages", response_model=list[PageSummaryOut])
def list_pages(document_id: str, user: UserDep, documents: DocumentsDep) -> list[PageSummaryOut]:
    """Page index for the reader's thumbnail strip (no page text)."""
    return [PageSummaryOut.model_validate(page) for page in documents.pages(user.id, document_id)]


@router.get("/documents/{document_id}/pages/{page_number}", response_model=PageOut)
def get_page(document_id: str, page_number: int, user: UserDep, documents: DocumentsDep) -> PageOut:
    return PageOut.model_validate(documents.page(user.id, document_id, page_number))


@router.get("/documents/{document_id}/topics", response_model=list[TopicOut])
def document_topics(
    document_id: str, user: UserDep, documents: DocumentsDep, topics: TopicsDep
) -> list[TopicOut]:
    document = documents.get(user.id, document_id)
    return [
        TopicOut.model_validate(topic)
        for topic in topics.list(user.id, document.course_id)
        if topic.document_id == document_id
    ]


@router.get("/documents/{document_id}/pages/{page_number}/preview")
def page_preview(
    document_id: str,
    page_number: int,
    context: ContextDep,
    user: UserDep,
    documents: DocumentsDep,
) -> Response:
    """Serve a rendered page image.

    The ownership check happens first, and the resolved path is confined to the
    preview directory so a crafted ``preview_path`` cannot escape it.
    """
    page = documents.page(user.id, document_id, page_number)
    if not page.preview_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No preview for this page."
        )
    try:
        data = context.blobs.read_asset(page.preview_path)
    except BlobNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Preview file is missing."
        ) from exc
    return Response(
        content=data,
        media_type="image/webp" if page.preview_path.endswith(".webp") else "image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/documents/{document_id}/file")
def document_file(
    document_id: str,
    context: ContextDep,
    user: UserDep,
    documents: DocumentsDep,
    drive: DriveDep,
    folders: LocalFoldersDep,
) -> Response:
    """Serve the PDF the reader renders client-side.

    For a PDF upload that is the original. For a PowerPoint deck it is the PDF
    the importer rendered from it, whose asset key the pipeline recorded in
    ``extraction_meta["rendered_pdf"]``. For a document read from Google
    Drive nothing is stored here: the bytes are fetched from Drive now and
    passed straight through, so the reader needs the connection to be live.
    """
    document = documents.get(user.id, document_id)
    rendered = (document.extraction_meta or {}).get("rendered_pdf", "")
    filename = document.original_filename
    try:
        if rendered:
            data = context.blobs.read_asset(rendered)
            filename = f"{Path(filename).stem}.pdf"
        elif document.source == "google_drive":
            from app.integrations.drive_import import fetch_from_drive
            from app.integrations.google_drive import DriveError

            try:
                data = fetch_from_drive(context, drive, user.id, document)
            except DriveError as exc:
                raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        elif document.source == "local_folder":
            from app.ingestion.local_folders import FolderError, read_in_place

            try:
                data = read_in_place(folders, user.id, document.source_ref)
            except FolderError as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        else:
            data = context.blobs.read(document.content_hash)
    except BlobNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The stored file is missing. Upload it again.",
        ) from exc
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": f'inline; filename="{filename}"',
        },
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: str,
    context: ContextDep,
    session: SessionDep,
    user: UserDep,
    documents: DocumentsDep,
    chunks: ChunksDep,
) -> None:
    """Remove a document and every index entry derived from it."""
    document = documents.get(user.id, document_id)
    remove_document(
        context=context, session=session, documents=documents, chunks=chunks, document=document
    )
