"""Process-wide runtime context.

Holds the objects that are expensive or stateful to construct — the database
engine, the embedded vector store (which holds a directory lock), the sparse
index and the embedding model — so they are built once at startup rather than
per request.  Request-scoped objects (sessions, repositories) are created per
call in :mod:`app.api.deps`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import Settings
from app.models.base import EmbeddingProvider
from app.models.registry import get_embedding_provider
from app.rag.sparse import SparseIndex
from app.rag.vector_store import VectorStore, build_vector_store
from app.storage.db import Database
from app.storage.files import BlobStore

log = logging.getLogger(__name__)


@dataclass
class RuntimeContext:
    settings: Settings
    database: Database
    blobs: BlobStore
    vector_store: VectorStore
    sparse_index: SparseIndex
    embedder: EmbeddingProvider

    @classmethod
    def create(cls, settings: Settings, *, database: Database | None = None) -> RuntimeContext:
        settings.ensure_dirs()

        # Materialise the API token now rather than on the first authenticated
        # request. The dev server's proxy and the Tauri shell both read it as
        # soon as the port is open, so creating it lazily would make the first
        # requests fail with 401 for no reason the user could diagnose.
        if not settings.auth_disabled:
            settings.resolve_api_token()

        database = database or Database.from_settings(settings)
        database.create_all()

        embedder = get_embedding_provider(settings)
        vector_store = build_vector_store(settings.vector_store, settings.vector_dir)
        vector_store.ensure_collection(embedder.dimensions)

        context = cls(
            settings=settings,
            database=database,
            blobs=BlobStore(settings.blob_dir),
            vector_store=vector_store,
            sparse_index=SparseIndex(database),
            embedder=embedder,
        )
        log.info(
            "runtime_ready",
            extra={
                "data_dir": str(settings.data_dir),
                "vector_backend": vector_store.backend,
                "embedding_model": embedder.model_id,
                "sparse_backend": "fts5" if context.sparse_index.use_fts else "python-bm25",
                "llm_provider": settings.llm_provider,
            },
        )
        return context

    def close(self) -> None:
        self.database.dispose()

    def health(self) -> dict[str, object]:
        return {
            "vector_backend": self.vector_store.backend,
            "vector_count": self.vector_store.count(),
            "sparse_backend": "fts5" if self.sparse_index.use_fts else "python-bm25",
            "embedding_model": self.embedder.model_id,
            "embedding_dimensions": self.embedder.dimensions,
            "llm_provider": self.settings.llm_provider,
            "data_dir": str(self.settings.data_dir),
        }
