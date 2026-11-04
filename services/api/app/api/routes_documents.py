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

from app.api.deps import (
    ChunksDep,
    ContextDep,
    CoursesDep,
    DocumentsDep,
    SessionDep,
    TopicsDep,
    UserDep,
    require_token,
)
from app.api.schemas import (
    DocumentOut,
    PageOut,
    PageSummaryOut,
    TopicOut,
    UploadResponse,
)
from app.ingestion.pipeline import UnsupportedFileError, ingest_document, validate_upload
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
    file: UploadFile = File(...),
    title: str = Form(default=""),
    kind: str = Form(default="lecture"),
) -> UploadResponse:
    courses.get(user.id, course_id)  # ownership check before touching the file

    data = await file.read()
    try:
        media_type = validate_upload(file.filename or "upload", data, context.settings)
    except UnsupportedFileError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    content_hash = context.blobs.hash_bytes(data)
    existing = documents.find_by_hash(user.id, course_id, content_hash)
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
        title=(title.strip() or Path(file.filename or "Untitled").stem)[:512],
        original_filename=(file.filename or "upload")[:512],
        media_type=media_type,
        content_hash=content_hash,
        byte_size=len(data),
        kind=kind,
        status="pending",
    )

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
    document_id: str, context: ContextDep, user: UserDep, documents: DocumentsDep
) -> Response:
    """Serve the original PDF so the reader can render it client-side."""
    document = documents.get(user.id, document_id)
    try:
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
            "Content-Disposition": f'inline; filename="{document.original_filename}"',
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
    content_hash = document.content_hash

    chunk_ids = chunks.delete_for_document(document_id)
    context.vector_store.delete(chunk_ids)
    context.sparse_index.delete_for_document(session, document_id)
    documents.delete(user.id, document_id)

    # Remove previews and, if no other document shares the bytes, the blob.
    context.blobs.delete_asset_prefix(document_id)

    from sqlalchemy import func, select

    from app.storage.models import Document

    remaining = session.scalar(
        select(func.count(Document.id)).where(Document.content_hash == content_hash)
    )
    if not remaining:
        context.blobs.delete(content_hash)
    log.info("document_deleted", extra={"document_id": document_id, "chunks": len(chunk_ids)})
