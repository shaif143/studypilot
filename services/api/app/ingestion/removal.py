"""Removing a document and everything derived from it.

One place, because two callers need exactly the same steps — the delete
button in the Library and a Drive sync replacing a file that changed — and a
step forgotten in one of them is an index entry that outlives its document.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.runtime import RuntimeContext
from app.storage.models import Document
from app.storage.repositories import ChunkRepository, DocumentRepository

log = logging.getLogger(__name__)


def remove_document(
    *,
    context: RuntimeContext,
    session: Session,
    documents: DocumentRepository,
    chunks: ChunkRepository,
    document: Document,
) -> None:
    """Delete the row, its chunks, both indexes, its assets, and the blob if unshared."""
    document_id = document.id
    user_id = document.user_id
    content_hash = document.content_hash

    chunk_ids = chunks.delete_for_document(document_id)
    context.vector_store.delete(chunk_ids)
    context.sparse_index.delete_for_document(session, document_id)
    documents.delete(user_id, document_id)

    # Previews and a rendered deck live under the document's id.
    context.blobs.delete_asset_prefix(document_id)

    # The original bytes are only ours to remove when nothing else shares them
    # — and were never stored at all for a document read from Drive.
    remaining = session.scalar(
        select(func.count(Document.id)).where(Document.content_hash == content_hash)
    )
    if not remaining:
        context.blobs.delete(content_hash)
    log.info("document_deleted", extra={"document_id": document_id, "chunks": len(chunk_ids)})
