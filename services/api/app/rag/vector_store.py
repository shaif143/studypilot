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
from typing import Any

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


class QdrantLocalVectorStore(VectorStore):
    """Embedded Qdrant persisted to a local directory."""

    def __init__(self, path: Path) -> None:
        from qdrant_client import QdrantClient

        path.mkdir(parents=True, exist_ok=True)
        self._client = QdrantClient(path=str(path))
        self._dimensions = 0

    @property
    def backend(self) -> str:
        return "qdrant-local"

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


def build_vector_store(backend: str, path: Path) -> VectorStore:
    """Build the configured store, degrading to numpy rather than failing."""
    if backend == "numpy":
        return NumpyVectorStore()
    if backend == "qdrant-local":
        try:
            return QdrantLocalVectorStore(path)
        except Exception as exc:
            log.warning(
                "qdrant_unavailable",
                extra={"reason": str(exc), "detail": "falling back to in-process store"},
            )
            return NumpyVectorStore()
    raise ValueError(f"Unknown vector store backend: {backend}")
