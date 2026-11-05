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
from app.neon_auth import NeonAuthVerifier
from app.rag.sparse import SparseIndex, build_sparse_index
from app.rag.vector_store import VectorStore, build_vector_store
from app.storage.db import Database
from app.storage.files import BlobStore, build_blob_store

log = logging.getLogger(__name__)


@dataclass
class RuntimeContext:
    settings: Settings
    database: Database
    blobs: BlobStore
    vector_store: VectorStore
    sparse_index: SparseIndex
    embedder: EmbeddingProvider
    #: Present only when identity is delegated to Neon Auth.
    neon_auth: NeonAuthVerifier | None = None

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

        # Loud, specific, and at boot: a hosted instance on an ephemeral disk
        # looks perfectly healthy right up until the first upload disappears.
        for warning in settings.configuration_warnings():
            log.warning("configuration_risk", extra={"detail": warning})

        embedder = get_embedding_provider(settings)
        vector_store = build_vector_store(
            settings.vector_store,
            settings.vector_dir,
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            database=database,
        )
        vector_store.ensure_collection(embedder.dimensions)

        blobs = build_blob_store(settings)
        # Fail at boot, in the deploy log, rather than on a student's first
        # upload. A misconfigured bucket is otherwise invisible until then.
        try:
            blobs.check_reachable()
        except RuntimeError as exc:
            if settings.is_hosted:
                raise
            log.warning("storage_unreachable", extra={"detail": str(exc)})

        # Built once: it caches the JWKS rather than fetching per request.
        neon_auth = NeonAuthVerifier(settings) if settings.uses_neon_auth else None

        context = cls(
            settings=settings,
            database=database,
            blobs=blobs,
            vector_store=vector_store,
            sparse_index=build_sparse_index(database),
            embedder=embedder,
            neon_auth=neon_auth,
        )
        log.info(
            "runtime_ready",
            extra={
                "data_dir": str(settings.data_dir),
                "vector_backend": vector_store.backend,
                "embedding_model": embedder.model_id,
                "sparse_backend": context.sparse_index.backend,
                "storage_backend": context.blobs.backend,
                "database": database.dialect,
                "mode": settings.deployment_mode,
                "llm_provider": settings.llm_provider,
                "auth_provider": settings.auth_provider,
            },
        )
        return context

    def close(self) -> None:
        self.database.dispose()

    def health(self) -> dict[str, object]:
        # The count is the one thing here that needs the database, so it is
        # also the thing that reveals a broken connection. Reported as null
        # with the reason rather than as a confident zero: a deployment whose
        # pooled DSN is wrong still starts, because schema work runs on the
        # direct connection, and then every query fails while the health
        # report looks like an empty but healthy instance.
        vector_count: int | None
        database_error: str | None = None
        try:
            vector_count = self.vector_store.count()
        except Exception as exc:
            vector_count = None
            database_error = f"{type(exc).__name__}: {exc}"[:300]
            log.warning("health_vector_count_failed", extra={"detail": database_error})

        return {
            "vector_backend": self.vector_store.backend,
            "vector_count": vector_count,
            "database_error": database_error,
            "sparse_backend": self.sparse_index.backend,
            "storage_backend": self.blobs.backend,
            "database": self.database.dialect,
            "deployment_mode": self.settings.deployment_mode,
            "embedding_model": self.embedder.model_id,
            "embedding_dimensions": self.embedder.dimensions,
            "llm_provider": self.settings.llm_provider,
            "auth_provider": self.settings.auth_provider,
            "neon_auth": self.neon_auth.health() if self.neon_auth else None,
            "data_dir": str(self.settings.data_dir),
        }
