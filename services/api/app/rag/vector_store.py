"""Dense vector storage behind a narrow interface.

Two implementations:

``qdrant-local``
    Qdrant running in embedded mode against a local directory.  The same client
    code points at a remote Qdrant cluster by changing the URL, which is why the
    interface exists.
``numpy``
    A dependency-light in-process store used by tests and as a fallback if the
    embedded Qdrant directory cannot be opened.

Payload filtering is applied by the store so access control cannot be forgotten
at the call site.
"""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.storage.db import Database

log = logging.getLogger(__name__)

COLLECTION = "studypilot_chunks"


@dataclass
class VectorRecord:
    chunk_id: str
    vector: list[float]
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class VectorHit:
    chunk_id: str
    score: float
    payload: dict[str, Any] = field(default_factory=dict)


class VectorStore(ABC):
    @property
    @abstractmethod
    def backend(self) -> str: ...

    @abstractmethod
    def ensure_collection(self, dimensions: int) -> None: ...

    @abstractmethod
    def upsert(self, records: list[VectorRecord]) -> None: ...

    @abstractmethod
    def search(
        self, vector: list[float], *, limit: int, allowed_chunk_ids: set[str] | None = None
    ) -> list[VectorHit]: ...

    @abstractmethod
    def delete(self, chunk_ids: list[str]) -> None: ...

    @abstractmethod
    def count(self) -> int: ...

    @abstractmethod
    def reset(self) -> None: ...


class NumpyVectorStore(VectorStore):
    """Exact cosine search over an in-process matrix.

    Fine for a single student's corpus (tens of thousands of chunks); it is
    exact rather than approximate, which makes retrieval tests deterministic.
    """

    def __init__(self) -> None:
        self._vectors: dict[str, list[float]] = {}
        self._payloads: dict[str, dict[str, Any]] = {}
        self._dimensions = 0

    @property
    def backend(self) -> str:
        return "numpy"

    def ensure_collection(self, dimensions: int) -> None:
        self._dimensions = dimensions

    def upsert(self, records: list[VectorRecord]) -> None:
        for record in records:
            self._vectors[record.chunk_id] = record.vector
            self._payloads[record.chunk_id] = record.payload

    def search(
        self, vector: list[float], *, limit: int, allowed_chunk_ids: set[str] | None = None
    ) -> list[VectorHit]:
        query_norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        hits: list[VectorHit] = []
        for chunk_id, stored in self._vectors.items():
            if allowed_chunk_ids is not None and chunk_id not in allowed_chunk_ids:
                continue
            if len(stored) != len(vector):
                continue
            dot = sum(a * b for a, b in zip(vector, stored, strict=True))
            stored_norm = math.sqrt(sum(value * value for value in stored)) or 1.0
            hits.append(
                VectorHit(
                    chunk_id=chunk_id,
                    score=dot / (query_norm * stored_norm),
                    payload=self._payloads.get(chunk_id, {}),
                )
            )
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:limit]

    def delete(self, chunk_ids: list[str]) -> None:
        for chunk_id in chunk_ids:
            self._vectors.pop(chunk_id, None)
            self._payloads.pop(chunk_id, None)

    def count(self) -> int:
        return len(self._vectors)

    def reset(self) -> None:
        self._vectors.clear()
        self._payloads.clear()


class QdrantVectorStore(VectorStore):
    """Qdrant, either embedded on local disk or a remote server.

    The two differ only in how the client is constructed, which is the whole
    reason this interface exists: a hosted deployment has no durable local
    disk, and swapping to a managed Qdrant cluster changes nothing above.
    """

    def __init__(
        self,
        *,
        path: Path | None = None,
        url: str = "",
        api_key: str = "",
    ) -> None:
        from qdrant_client import QdrantClient

        if url:
            self._client = QdrantClient(url=url, api_key=api_key or None, timeout=30)
            self._backend = "qdrant-remote"
        else:
            if path is None:
                raise ValueError("An embedded Qdrant store needs a path.")
            path.mkdir(parents=True, exist_ok=True)
            self._client = QdrantClient(path=str(path))
            self._backend = "qdrant-local"
        self._dimensions = 0

    @property
    def backend(self) -> str:
        return self._backend

    def ensure_collection(self, dimensions: int) -> None:
        from qdrant_client import models as qmodels

        self._dimensions = dimensions
        existing = {c.name for c in self._client.get_collections().collections}
        if COLLECTION in existing:
            info = self._client.get_collection(COLLECTION)
            current = info.config.params.vectors
            size = getattr(current, "size", None)
            if size is not None and int(size) != dimensions:
                # The embedding model changed; the old vectors are meaningless.
                log.warning(
                    "embedding_dimension_changed",
                    extra={"old": int(size), "new": dimensions},
                )
                self._client.delete_collection(COLLECTION)
            else:
                return
        self._client.create_collection(
            collection_name=COLLECTION,
            vectors_config=qmodels.VectorParams(size=dimensions, distance=qmodels.Distance.COSINE),
        )

    @staticmethod
    def _point_id(chunk_id: str) -> str:
        # Chunk ids are already uuid4 hex, which Qdrant accepts as a UUID.
        return (
            f"{chunk_id[:8]}-{chunk_id[8:12]}-{chunk_id[12:16]}-{chunk_id[16:20]}-{chunk_id[20:32]}"
        )

    def upsert(self, records: list[VectorRecord]) -> None:
        from qdrant_client import models as qmodels

        if not records:
            return
        self._client.upsert(
            collection_name=COLLECTION,
            points=[
                qmodels.PointStruct(
                    id=self._point_id(record.chunk_id),
                    vector=record.vector,
                    payload={**record.payload, "chunk_id": record.chunk_id},
                )
                for record in records
            ],
        )

    def search(
        self, vector: list[float], *, limit: int, allowed_chunk_ids: set[str] | None = None
    ) -> list[VectorHit]:
        from qdrant_client import models as qmodels

        query_filter = None
        if allowed_chunk_ids is not None:
            if not allowed_chunk_ids:
                return []
            # Scope by user/course rather than by id when the allow-list is huge;
            # for a single student's corpus an id match stays cheap.
            query_filter = qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="chunk_id", match=qmodels.MatchAny(any=sorted(allowed_chunk_ids))
                    )
                ]
            )
        response = self._client.query_points(
            collection_name=COLLECTION,
            query=vector,
            limit=limit,
            query_filter=query_filter,
            with_payload=True,
        )
        return [
            VectorHit(
                chunk_id=str((point.payload or {}).get("chunk_id", "")),
                score=float(point.score),
                payload=dict(point.payload or {}),
            )
            for point in response.points
            if (point.payload or {}).get("chunk_id")
        ]

    def delete(self, chunk_ids: list[str]) -> None:
        from qdrant_client import models as qmodels

        if not chunk_ids:
            return
        self._client.delete(
            collection_name=COLLECTION,
            points_selector=qmodels.PointIdsList(
                points=[self._point_id(chunk_id) for chunk_id in chunk_ids]
            ),
        )

    def count(self) -> int:
        try:
            return int(self._client.count(COLLECTION, exact=True).count)
        except Exception:
            return 0

    def reset(self) -> None:
        with suppress(Exception):
            self._client.delete_collection(COLLECTION)
        if self._dimensions:
            self.ensure_collection(self._dimensions)


class PgVectorStore(VectorStore):
    """Dense search inside PostgreSQL, using the pgvector extension.

    Worth having because of what it removes: with this, a hosted deployment's
    entire state is one Postgres database plus an object store. No separate
    vector service to provision, pay for, secure, or keep in sync — and the
    scope filter runs in the same query as the similarity search rather than
    as an allow-list shipped to another system.

    An HNSW index gives approximate search; it is built once the table exists
    and is what keeps queries fast as a corpus grows.
    """

    TABLE = "chunk_vectors"

    def __init__(self, database: Database) -> None:
        self.database = database
        self._dimensions = 0

    @property
    def backend(self) -> str:
        return "pgvector"

    def _as_literal(self, vector: list[float]) -> str:
        # pgvector accepts its own text form: '[1,2,3]'.
        return "[" + ",".join(repr(float(value)) for value in vector) + "]"

    def ensure_collection(self, dimensions: int) -> None:
        from sqlalchemy import text as sql_text

        self._dimensions = dimensions
        with self.database.engine.begin() as conn:
            conn.execute(sql_text("CREATE EXTENSION IF NOT EXISTS vector"))

            existing = conn.execute(
                sql_text(
                    "SELECT a.atttypmod FROM pg_attribute a "
                    "JOIN pg_class c ON c.oid = a.attrelid "
                    f"WHERE c.relname = '{self.TABLE}' AND a.attname = 'embedding'"
                )
            ).first()
            if existing is not None and int(existing[0]) != dimensions:
                # The embedding model changed; the stored vectors are
                # meaningless and cannot be compared with the new ones.
                log.warning(
                    "embedding_dimension_changed",
                    extra={"old": int(existing[0]), "new": dimensions},
                )
                conn.execute(sql_text(f"DROP TABLE IF EXISTS {self.TABLE}"))

            conn.execute(
                sql_text(
                    f"CREATE TABLE IF NOT EXISTS {self.TABLE} ("
                    "  chunk_id TEXT PRIMARY KEY,"
                    "  user_id TEXT NOT NULL,"
                    "  course_id TEXT NOT NULL,"
                    "  document_id TEXT NOT NULL,"
                    f"  embedding vector({dimensions}) NOT NULL"
                    ")"
                )
            )
            conn.execute(
                sql_text(
                    f"CREATE INDEX IF NOT EXISTS ix_{self.TABLE}_embedding "
                    f"ON {self.TABLE} USING hnsw (embedding vector_cosine_ops)"
                )
            )
            conn.execute(
                sql_text(
                    f"CREATE INDEX IF NOT EXISTS ix_{self.TABLE}_scope "
                    f"ON {self.TABLE} (user_id, course_id, document_id)"
                )
            )

    def upsert(self, records: list[VectorRecord]) -> None:
        from sqlalchemy import text as sql_text

        if not records:
            return
        payload = [
            {
                "chunk_id": record.chunk_id,
                "user_id": str(record.payload.get("user_id", "")),
                "course_id": str(record.payload.get("course_id", "")),
                "document_id": str(record.payload.get("document_id", "")),
                "embedding": self._as_literal(record.vector),
            }
            for record in records
        ]
        with self.database.engine.begin() as conn:
            conn.execute(
                sql_text(
                    f"INSERT INTO {self.TABLE} "
                    "(chunk_id, user_id, course_id, document_id, embedding) VALUES "
                    "(:chunk_id, :user_id, :course_id, :document_id, CAST(:embedding AS vector)) "
                    "ON CONFLICT (chunk_id) DO UPDATE SET embedding = EXCLUDED.embedding"
                ),
                payload,
            )

    def search(
        self, vector: list[float], *, limit: int, allowed_chunk_ids: set[str] | None = None
    ) -> list[VectorHit]:
        from sqlalchemy import text as sql_text

        if allowed_chunk_ids is not None and not allowed_chunk_ids:
            return []

        params: dict[str, Any] = {"query": self._as_literal(vector), "limit": limit}
        where = ""
        if allowed_chunk_ids is not None:
            # The allow-list is the access-control boundary; applying it here
            # means the database never even ranks another user's rows.
            params["allowed"] = list(allowed_chunk_ids)
            where = "WHERE chunk_id = ANY(:allowed)"

        with self.database.engine.connect() as conn:
            rows = conn.execute(
                sql_text(
                    "SELECT chunk_id, user_id, course_id, document_id, "
                    "       1 - (embedding <=> CAST(:query AS vector)) AS score "
                    f"FROM {self.TABLE} {where} "
                    "ORDER BY embedding <=> CAST(:query AS vector) LIMIT :limit"
                ),
                params,
            ).all()

        return [
            VectorHit(
                chunk_id=str(row[0]),
                score=float(row[4]),
                payload={
                    "user_id": row[1],
                    "course_id": row[2],
                    "document_id": row[3],
                },
            )
            for row in rows
        ]

    def delete(self, chunk_ids: list[str]) -> None:
        from sqlalchemy import text as sql_text

        if not chunk_ids:
            return
        with self.database.engine.begin() as conn:
            conn.execute(
                sql_text(f"DELETE FROM {self.TABLE} WHERE chunk_id = ANY(:ids)"),
                {"ids": list(chunk_ids)},
            )

    def count(self) -> int:
        from sqlalchemy import text as sql_text

        try:
            with self.database.engine.connect() as conn:
                return int(
                    conn.execute(sql_text(f"SELECT count(*) FROM {self.TABLE}")).scalar() or 0
                )
        except Exception:
            return 0

    def reset(self) -> None:
        from sqlalchemy import text as sql_text

        with self.database.engine.begin() as conn:
            conn.execute(sql_text(f"TRUNCATE TABLE {self.TABLE}"))


def build_vector_store(
    backend: str,
    path: Path,
    *,
    url: str = "",
    api_key: str = "",
    database: Database | None = None,
) -> VectorStore:
    """Build the configured store.

    An embedded Qdrant that will not open degrades to the in-process store so
    the desktop app still works. A *remote* store does not degrade: silently
    falling back would mean a hosted deployment answered from an empty index
    while appearing healthy, so the failure is raised.
    """
    if backend == "numpy":
        return NumpyVectorStore()
    if backend == "pgvector":
        if database is None or database.dialect != "postgresql":
            raise ValueError("pgvector requires STUDYPILOT_DATABASE_URL to be PostgreSQL.")
        return PgVectorStore(database)
    if backend == "qdrant-remote":
        if not url:
            raise ValueError("qdrant-remote requires STUDYPILOT_QDRANT_URL.")
        return QdrantVectorStore(url=url, api_key=api_key)
    if backend == "qdrant-local":
        try:
            return QdrantVectorStore(path=path)
        except Exception as exc:
            log.warning(
                "qdrant_unavailable",
                extra={"reason": str(exc), "detail": "falling back to in-process store"},
            )
            return NumpyVectorStore()
    raise ValueError(f"Unknown vector store backend: {backend}")
