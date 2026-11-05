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
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.ingestion.chunking import build_chunks
from app.ingestion.media import MediaReader, is_audio, is_image, sniff_media_type
from app.ingestion.pdf import (
    ExtractedDocument,
    PageElement,
    UnreadableDocumentError,
    extract_pdf,
)
from app.ingestion.ppt import PPT_MEDIA_TYPE, looks_like_ppt, render_ppt_to_pdf
from app.ingestion.pptx import (
    PPTX_MEDIA_TYPE,
    UnreadablePresentationError,
    looks_like_pptx,
    render_pptx_to_pdf,
)
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
    sniffed = sniff_media_type(data, filename)
    if sniffed and (is_image(sniffed) or is_audio(sniffed)):
        return sniffed
    if looks_like_pptx(data, filename):
        return PPTX_MEDIA_TYPE
    if looks_like_ppt(data, filename):
        return PPT_MEDIA_TYPE
    raise UnsupportedFileError(
        "Supported files are PDF, PowerPoint (.pptx and .ppt), PNG, JPEG, WebP images, WAV, "
        "MP3 or M4A audio, and .zip or .rar archives of those. This file does not look like "
        "any of those."
    )


_SCAN_HINT = (
    " With cloud AI processing on and a Claude or Gemini key, StudyPilot reads the "
    "pages as images instead."
)


def _read_scanned_pages(
    extracted: ExtractedDocument,
    source_path: Path,
    document: Document,
    settings: Settings,
    media_reader: MediaReader | None,
) -> None:
    """A PDF with no text layer: read each page image with the vision model.

    Only when the caller supplied a reader whose provider can read images —
    which carries the user's consent — and only up to ``max_ocr_pages``.
    Without one, the document stays partial with a hint saying what would
    read it. The page text replaces the empty extraction in place so topics,
    chunks and citations are built exactly as for a born-digital PDF.
    """
    why = media_reader.cannot_read("image/png") if media_reader is not None else "no reader"
    if why:
        extracted.warnings = [
            w + (_SCAN_HINT if "No text" in w else "") for w in extracted.warnings
        ]
        return
    assert media_reader is not None
    import pymupdf

    limit = min(extracted.page_count, settings.max_ocr_pages)
    read = 0
    failures: list[str] = []
    with pymupdf.open(source_path) as pdf:
        for page in extracted.pages[:limit]:
            try:
                pix = pdf[page.page_number - 1].get_pixmap(dpi=150)
                text = media_reader.read_page_image(
                    pix.tobytes("png"), title=document.title, page_number=page.page_number
                ).strip()
            except Exception as exc:
                failures.append(f"page {page.page_number}: {exc}")
                continue
            if not text:
                continue
            page.text = text
            first = text.splitlines()[0].strip() if text else ""
            page.heading = first[:200] if len(first) <= 120 else ""
            page.elements = [
                PageElement(
                    index=0, kind="text", text=text, bbox=[0.0, 0.0, page.width, page.height]
                )
            ]
            read += 1
    label = media_reader.provider.info.label if media_reader.provider else "a vision model"
    extracted.warnings = [w for w in extracted.warnings if "No text could be extracted" not in w]
    if read:
        extracted.warnings.insert(
            0,
            f"This document had no text layer; {read} of {extracted.page_count} pages were "
            f"read from their images by {label}. Check against the original before relying "
            "on it.",
        )
        extracted.extractor = f"{extracted.extractor}+vision:{media_reader.provider.info.key}"
    else:
        extracted.warnings.insert(0, f"{label} returned no text for the scanned pages.")
    if extracted.page_count > limit:
        extracted.warnings.append(
            f"Only the first {limit} scanned pages were read (the limit for one document)."
        )
    if failures:
        extracted.warnings.append("Some pages could not be read: " + "; ".join(failures[:3]))
    extracted.partial = 0 < extracted.text_pages < extracted.page_count


@contextmanager
def _source_file(
    blobs: BlobStore,
    document: Document,
    source_bytes: bytes | None,
    source_path: Path | None = None,
) -> Iterator[Path]:
    """A path to the document's bytes: a file in place, the stored blob, or a temporary copy."""
    if source_path is not None:
        yield source_path
        return
    if source_bytes is None:
        with blobs.local_path(document.content_hash) as path:
            yield Path(path)
        return
    suffix = Path(document.original_filename).suffix.lower() or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as handle:
        handle.write(source_bytes)
        handle.flush()
        yield Path(handle.name)


def _extract_presentation(
    source_path: Path,
    document: Document,
    settings: Settings,
    blobs: BlobStore,
    preview_sink,
) -> ExtractedDocument:
    """Render a .pptx to PDF, keep that PDF as an asset, and extract it like one.

    The rendered PDF is what the reader shows and what citations highlight, so
    it is stored next to the page previews and its key recorded on the
    document; ``/documents/{id}/file`` serves it in place of the original.
    """
    render = render_ppt_to_pdf if document.media_type == PPT_MEDIA_TYPE else render_pptx_to_pdf
    deck = render(source_path, max_slides=settings.max_pdf_pages)
    rendered_key = blobs.put_asset(f"{document.id}/rendered.pdf", deck.pdf, "application/pdf")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as handle:
        handle.write(deck.pdf)
        handle.flush()
        extracted = extract_pdf(
            Path(handle.name), max_pages=settings.max_pdf_pages, preview_sink=preview_sink
        )
    extracted.extractor = (
        "olefile+pymupdf" if document.media_type == PPT_MEDIA_TYPE else "python-pptx+pymupdf"
    )
    extracted.warnings = deck.warnings + extracted.warnings
    if deck.title and not extracted.title:
        extracted.title = deck.title
    extracted.rendered_pdf_key = rendered_key  # carried to `extraction_meta` by the caller
    return extracted


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
    media_reader: MediaReader | None = None,
    source_bytes: bytes | None = None,
    source_path: Path | None = None,
    keep_previews: bool = True,
) -> IngestionResult:
    """Extract, chunk, embed and index a document.

    PDFs are read deterministically. Image and audio briefs go through
    ``media_reader``, which carries whatever consent-gated model the caller
    resolved; without one, such a document fails with a message saying what
    is needed rather than being indexed empty.

    Normally the bytes come from the blob store under ``content_hash``. A
    document that is *not* kept here passes them as ``source_bytes`` (read
    from Google Drive; written to a temporary file for the extractor and
    discarded) or names the file in place as ``source_path`` (a linked folder
    on this machine; nothing is copied at all). ``keep_previews=False`` also skips the
    rendered page images, so such a document costs only its text index.
    """
    started = time.monotonic()
    documents.set_status(document, "processing", "Reading the document")

    def store_preview(page_number: int, data: bytes, media_type: str) -> str:
        suffix = "webp" if media_type.endswith("webp") else "png"
        return blobs.put_asset(f"{document.id}/page-{page_number:04d}.{suffix}", data, media_type)

    preview_sink = store_preview if keep_previews else None

    try:
        # The store yields a real file: PyMuPDF wants a path, and a remote
        # store downloads to a temporary one that is cleaned up on exit.
        with _source_file(blobs, document, source_bytes, source_path) as working_path:
            extracted: ExtractedDocument
            if is_image(document.media_type) or is_audio(document.media_type):
                reader = media_reader or MediaReader(settings=settings, provider=None)
                data = Path(working_path).read_bytes()
                if is_image(document.media_type):
                    extracted = reader.read_image(
                        data, document.media_type, title=document.title, preview_sink=store_preview
                    )
                else:
                    extracted = reader.read_audio(
                        data,
                        document.media_type,
                        title=document.title,
                        source_path=Path(working_path),
                    )
            elif document.media_type in (PPTX_MEDIA_TYPE, PPT_MEDIA_TYPE):
                extracted = _extract_presentation(
                    Path(working_path), document, settings, blobs, preview_sink
                )
            else:
                extracted = extract_pdf(
                    working_path,
                    max_pages=settings.max_pdf_pages,
                    preview_sink=preview_sink,
                )
                if extracted.text_pages == 0 and extracted.page_count > 0:
                    _read_scanned_pages(
                        extracted, Path(working_path), document, settings, media_reader
                    )
    except (UnreadableDocumentError, UnreadablePresentationError) as exc:
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
        "extractor": extracted.extractor,
    }
    if extracted.rendered_pdf_key:
        document.extraction_meta = {
            **document.extraction_meta,
            "rendered_pdf": extracted.rendered_pdf_key,
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
