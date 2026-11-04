"""Document ingestion orchestration.

Deterministic end-to-end: validate → extract → detect topics → chunk → embed →
index (dense + sparse) → persist provenance.  No language model is involved, so
ingestion works identically with or without a model configured, and its output
is reproducible.

Partial failure is a first-class outcome.  A deck where half the slides are
images still gets indexed, and the document is marked ``partial`` with the
reason attached rather than reported as a success.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.ingestion.chunking import build_chunks
from app.ingestion.pdf import ExtractedDocument, UnreadableDocumentError, extract_pdf
from app.ingestion.topics import (
    detect_topics,
    section_for_pages,
    topic_slug_for_pages,
)
from app.models.base import EmbeddingProvider
from app.rag.sparse import SparseIndex
from app.rag.vector_store import VectorRecord, VectorStore
from app.storage.files import BlobStore
from app.storage.models import Chunk, Document, Page
from app.storage.repositories import ChunkRepository, DocumentRepository, TopicRepository

log = logging.getLogger(__name__)

#: Magic bytes for a PDF. Content type from the browser is not trusted.
_PDF_MAGIC = b"%PDF-"


class UnsupportedFileError(ValueError):
    pass


@dataclass
class IngestionResult:
    document_id: str
    status: str  # indexed | partial | failed
    page_count: int
    chunk_count: int
    topic_count: int
    warnings: list[str] = field(default_factory=list)
    duration_ms: int = 0
    embedding_model: str = ""


def validate_upload(filename: str, data: bytes, settings: Settings) -> str:
    """Validate an upload and return its media type.

    Checks the magic bytes rather than the declared content type, and enforces
    a size limit before anything is written to disk.
    """
    if not data:
        raise UnsupportedFileError("The uploaded file is empty.")
    if len(data) > settings.max_upload_bytes:
        limit_mb = settings.max_upload_bytes // (1024 * 1024)
        raise UnsupportedFileError(f"That file is larger than the {limit_mb} MB limit.")
    if data[:5] == _PDF_MAGIC:
        return "application/pdf"
    suffix = Path(filename).suffix.lower()
    if suffix == ".pptx":
        raise UnsupportedFileError(
            "PowerPoint import is not implemented yet. Export the deck as a PDF and upload that."
        )
    raise UnsupportedFileError(
        "Only PDF files are supported at the moment. The file does not look like a PDF."
    )


def ingest_document(
    *,
    document: Document,
    settings: Settings,
    blobs: BlobStore,
    embedder: EmbeddingProvider,
    vector_store: VectorStore,
    sparse_index: SparseIndex,
    documents: DocumentRepository,
    chunks: ChunkRepository,
    topics: TopicRepository,
) -> IngestionResult:
    """Extract, chunk, embed and index a stored document."""
    started = time.monotonic()
    documents.set_status(document, "processing", "Reading the document")

    def store_preview(page_number: int, data: bytes, media_type: str) -> str:
        suffix = "webp" if media_type.endswith("webp") else "png"
        return blobs.put_asset(f"{document.id}/page-{page_number:04d}.{suffix}", data, media_type)

    try:
        # The store yields a real file: PyMuPDF wants a path, and a remote
        # store downloads to a temporary one that is cleaned up on exit.
        with blobs.local_path(document.content_hash) as source_path:
            extracted: ExtractedDocument = extract_pdf(
                source_path,
                max_pages=settings.max_pdf_pages,
                preview_sink=store_preview,
            )
    except UnreadableDocumentError as exc:
        documents.set_status(document, "failed", str(exc))
        return IngestionResult(
            document_id=document.id,
            status="failed",
            page_count=0,
            chunk_count=0,
            topic_count=0,
            warnings=[str(exc)],
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    # --- pages -----------------------------------------------------------
    session = documents.session
    for extracted_page in extracted.pages:
        session.add(
            Page(
                document_id=document.id,
                page_number=extracted_page.page_number,
                heading=extracted_page.heading[:512],
                text=extracted_page.text,
                width=extracted_page.width,
                height=extracted_page.height,
                preview_path=extracted_page.preview_key,
                elements=[element.to_dict() for element in extracted_page.elements],
                has_equations=extracted_page.has_equations,
                has_figures=extracted_page.has_figures,
                has_tables=extracted_page.has_tables,
            )
        )
    session.flush()

    document.page_count = extracted.page_count
    if extracted.title and not document.title.strip():
        document.title = extracted.title[:512]
    document.extraction_meta = {
        "text_pages": extracted.text_pages,
        "pages_with_equations": sum(1 for p in extracted.pages if p.has_equations),
        "pages_with_figures": sum(1 for p in extracted.pages if p.has_figures),
        "pages_with_tables": sum(1 for p in extracted.pages if p.has_tables),
        "extractor": "pymupdf",
    }

    # --- topics ----------------------------------------------------------
    topic_drafts = detect_topics(extracted.pages, fallback_title=document.title)
    stored_topics = topics.upsert_many(
        document.user_id,
        document.course_id,
        [
            {
                "name": draft.name,
                "slug": draft.slug,
                "summary": draft.summary,
                "document_id": document.id,
                "first_page": draft.first_page,
                "last_page": draft.last_page,
            }
            for draft in topic_drafts
        ],
    )

    # --- chunks ----------------------------------------------------------
    drafts = build_chunks(
        extracted.pages,
        child_chars=settings.child_chunk_chars,
        child_overlap=settings.child_chunk_overlap,
        section_for_page=section_for_pages(topic_drafts, extracted.pages),
        topic_for_page=topic_slug_for_pages(topic_drafts),
    )

    if not drafts:
        detail = "; ".join(extracted.warnings) or "No text could be extracted."
        documents.set_status(document, "partial", detail)
        return IngestionResult(
            document_id=document.id,
            status="partial",
            page_count=extracted.page_count,
            chunk_count=0,
            topic_count=len(stored_topics),
            warnings=extracted.warnings,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    model_id = embedder.model_id
    rows: list[Chunk] = []
    by_ordinal: dict[int, Chunk] = {}
    for draft in drafts:
        row = Chunk(
            user_id=document.user_id,
            course_id=document.course_id,
            document_id=document.id,
            level=draft.level,
            page_number=draft.page_number,
            page_end=draft.page_end,
            section=draft.section[:512],
            topic_slug=draft.topic_slug,
            element_type=draft.element_type,
            text=draft.text,
            bbox=draft.bbox,
            token_estimate=draft.token_estimate,
            document_version=document.version,
            embedding_model=model_id if draft.level == "child" else "",
            ordinal=draft.ordinal,
        )
        rows.append(row)
        by_ordinal[draft.ordinal] = row

    chunks.add_all(rows)
    # Link children to their persisted parents now that ids exist.
    for draft in drafts:
        if draft.parent_ordinal is not None:
            parent = by_ordinal.get(draft.parent_ordinal)
            if parent is not None:
                by_ordinal[draft.ordinal].parent_id = parent.id
    session.flush()

    children = [row for row in rows if row.level == "child"]
    warnings = list(extracted.warnings)

    # --- dense index -----------------------------------------------------
    try:
        vectors = embedder.embed_documents([row.text for row in children])
        vector_store.ensure_collection(embedder.dimensions)
        vector_store.upsert(
            [
                VectorRecord(
                    chunk_id=row.id,
                    vector=vector,
                    payload={
                        "user_id": row.user_id,
                        "course_id": row.course_id,
                        "document_id": row.document_id,
                        "page_number": row.page_number,
                    },
                )
                for row, vector in zip(children, vectors, strict=True)
            ]
        )
    except Exception as exc:
        log.warning("dense_index_failed", extra={"document_id": document.id, "reason": str(exc)})
        warnings.append(
            "Semantic search could not be built for this document; keyword search still works."
        )

    # --- sparse index ----------------------------------------------------
    try:
        sparse_index.index_chunks(
            session,
            [
                {
                    "chunk_id": row.id,
                    "user_id": row.user_id,
                    "course_id": row.course_id,
                    "document_id": row.document_id,
                    "text": row.text,
                }
                for row in children
            ],
        )
    except Exception as exc:
        log.warning("sparse_index_failed", extra={"document_id": document.id, "reason": str(exc)})
        warnings.append("Keyword search could not be built for this document.")

    document.embedding_model = model_id
    status = "partial" if (extracted.partial or extracted.text_pages == 0) else "indexed"
    documents.set_status(document, status, "; ".join(warnings))

    duration_ms = int((time.monotonic() - started) * 1000)
    log.info(
        "document_indexed",
        extra={
            "document_id": document.id,
            "pages": extracted.page_count,
            "chunks": len(children),
            "topics": len(stored_topics),
            "status": status,
            "duration_ms": duration_ms,
        },
    )
    return IngestionResult(
        document_id=document.id,
        status=status,
        page_count=extracted.page_count,
        chunk_count=len(children),
        topic_count=len(stored_topics),
        warnings=warnings,
        duration_ms=duration_ms,
        embedding_model=model_id,
    )
