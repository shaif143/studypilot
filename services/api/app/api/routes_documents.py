"""Document upload, reading and deletion.

Upload is synchronous on purpose for the first release: a student uploading a
lecture deck wants to know whether it worked, and a 25-slide PDF indexes in
well under a second with the local pipeline.  The response carries the real
ingestion outcome — page count, chunk count, and any extraction warnings — so
the UI never has to claim success it cannot verify.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
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
    DocumentUpdate,
    PageOut,
    PageSummaryOut,
    TopicOut,
    UploadResponse,
)
from app.ingestion.archives import UnreadableArchiveError, archive_kind, unpack
from app.ingestion.media import MediaReader, is_audio, is_image, normalise_image, sniff_media_type
from app.ingestion.originals import OriginalUnavailableError, read_original
from app.ingestion.pipeline import UnsupportedFileError, ingest_document, validate_upload
from app.ingestion.removal import remove_document
from app.storage.files import BlobNotFoundError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["documents"], dependencies=[Depends(require_token)])

_upload_lock = threading.Lock()


@router.post(
    "/courses/{course_id}/documents",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    course_id: str,
    request: Request,
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
    #: The folder of this course to file it in, if any.
    folder_id: str = Form(default=""),
    #: Per-request consent to read an image or audio brief with a cloud model,
    #: required in addition to the stored setting. Ignored for PDFs, which are
    #: read on this machine without any model.
    allow_cloud: bool = Form(default=False),
) -> UploadResponse:
    courses.get(user.id, course_id)  # ownership check before touching the file
    data = await file.read()
    try:
        # Off the event loop, so a cancel (the client hanging up) is seen and
        # the rest of the app stays responsive while a long file is read.
        result = await run_in_threadpool(
            _ingest_upload,
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
    if await request.is_disconnected():
        _discard_cancelled(context, documents, chunks, user.id, [result])
        return result
    if folder_id:
        from app.storage.repositories import FolderRepository, NotFoundError

        try:
            folder = FolderRepository(documents.session).get(user.id, folder_id)
        except NotFoundError:
            folder = None
        if folder is not None and folder.course_id == course_id:
            documents.get(user.id, result.document.id).folder_id = folder.id
            documents.session.flush()
            result.document.folder_id = folder.id
    return result


def _discard_cancelled(
    context: ContextDep,
    documents: DocumentsDep,
    chunks: ChunksDep,
    user_id: str,
    results: list[UploadResponse],
) -> None:
    """An upload whose sender cancelled is not kept.

    Cancelling closes the connection; the file may already have been indexed
    by then. A copy that was in the course before this upload is left alone.
    """
    from app.storage.repositories import NotFoundError

    for result in results:
        if result.deduplicated:
            continue
        try:
            document = documents.get(user_id, result.document.id)
        except NotFoundError:
            continue
        remove_document(
            context=context,
            session=documents.session,
            documents=documents,
            chunks=chunks,
            document=document,
        )
        log.info("upload_cancelled", extra={"document_id": result.document.id})


@router.post(
    "/courses/{course_id}/documents/archive",
    response_model=ArchiveUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_archive(
    course_id: str,
    request: Request,
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
    cancelled = False
    for member in unpacked.members:
        if await request.is_disconnected():
            cancelled = True
            break
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
            result = await run_in_threadpool(
                _ingest_upload,
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
    if cancelled or await request.is_disconnected():
        _discard_cancelled(
            context, documents, chunks, user.id, [m.result for m in results if m.result is not None]
        )
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
    # Indexing runs off the event loop now, so two uploads could otherwise
    # write the vector store and the search index at the same moment. One at a
    # time, as it always was when the loop itself did the serialising.
    with _upload_lock:
        return _ingest_upload_locked(
            filename=filename,
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


def _ingest_upload_locked(
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


@router.patch("/documents/{document_id}", response_model=DocumentOut)
def update_document(
    document_id: str, payload: DocumentUpdate, user: UserDep, documents: DocumentsDep
) -> DocumentOut:
    """Say what a document is, or file it in one of its course's folders."""
    from app.storage.repositories import FolderRepository

    document = documents.get(user.id, document_id)
    if payload.kind is not None:
        document.kind = payload.kind
    if payload.folder_id is not None:
        if payload.folder_id == "":
            document.folder_id = None
        else:
            folder = FolderRepository(documents.session).get(user.id, payload.folder_id)
            if folder.course_id != document.course_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="That folder belongs to another course.",
                )
            document.folder_id = folder.id
    documents.session.flush()
    return DocumentOut.model_validate(document)


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
    try:
        data, filename = read_original(context, user.id, document, drive, folders)
    except OriginalUnavailableError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
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
