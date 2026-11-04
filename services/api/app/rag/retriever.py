"""Hybrid retrieval with access control and an evidence-sufficiency check.

Order of operations matters and is enforced here:

1. Resolve the caller's permitted scope (user, courses, documents) in the
   database.  Nothing outside that scope can be retrieved, because the
   allow-list is passed into both retrievers.
2. Classify the query to pick retriever weights.
3. Run dense and sparse retrieval over the permitted scope.
4. Fuse with RRF, then optionally rerank.
5. Expand each child hit to its parent chunk for readable context.
6. Judge whether the evidence is sufficient; the caller uses that to decide
   whether to answer, to retry with a reformulated query, or to abstain.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.models.base import EmbeddingProvider
from app.rag.fusion import reciprocal_rank_fusion
from app.rag.sparse import SparseIndex, content_terms
from app.rag.vector_store import VectorStore
from app.storage.models import Chunk
from app.storage.repositories import ChunkRepository, DocumentRepository

log = logging.getLogger(__name__)

#: Question shapes that favour one retriever over the other.
_DEFINITION_RE = re.compile(
    r"\b(what is|what are|define|definition of|meaning of|stands for)\b", re.IGNORECASE
)
_FORMULA_RE = re.compile(
    r"\b(formula|equation|derive|derivation|calculate|compute|prove)\b", re.IGNORECASE
)
_HOWWHY_RE = re.compile(
    r"\b(why|how does|how do|explain|intuition|difference between)\b", re.IGNORECASE
)


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    page_number: int
    document_id: str
    document_title: str
    section: str
    topic_slug: str
    element_type: str
    bbox: list[float] | None
    score: float
    retrievers: list[str] = field(default_factory=list)
    #: The page-level parent text, used as model context.
    parent_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "page_number": self.page_number,
            "document_id": self.document_id,
            "document_title": self.document_title,
            "section": self.section,
            "topic_slug": self.topic_slug,
            "element_type": self.element_type,
            "bbox": self.bbox,
            "score": round(self.score, 6),
            "retrievers": self.retrievers,
            "parent_text": self.parent_text,
        }


@dataclass
class RetrievalOutcome:
    chunks: list[RetrievedChunk]
    query: str
    #: "definition" | "formula" | "explanation" | "general"
    query_class: str
    sufficient: bool
    #: Human-readable reason shown in the UI when evidence is thin.
    reason: str = ""
    #: Set when a corrective second pass was run.
    reformulated_query: str | None = None
    scope_chunk_count: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.chunks


def classify_query(query: str) -> str:
    if _FORMULA_RE.search(query):
        return "formula"
    if _DEFINITION_RE.search(query):
        return "definition"
    if _HOWWHY_RE.search(query):
        return "explanation"
    return "general"


#: Weights per query class: definitions and formulas reward exact term matches.
_WEIGHTS: dict[str, dict[str, float]] = {
    "definition": {"dense": 0.8, "sparse": 1.4},
    "formula": {"dense": 0.7, "sparse": 1.5},
    "explanation": {"dense": 1.3, "sparse": 0.9},
    "general": {"dense": 1.0, "sparse": 1.0},
}


class HybridRetriever:
    def __init__(
        self,
        *,
        settings: Settings,
        embedder: EmbeddingProvider,
        vector_store: VectorStore,
        sparse_index: SparseIndex,
        chunks: ChunkRepository,
        documents: DocumentRepository,
    ) -> None:
        self.settings = settings
        self.embedder = embedder
        self.vector_store = vector_store
        self.sparse_index = sparse_index
        self.chunk_repo = chunks
        self.document_repo = documents

    def retrieve(
        self,
        query: str,
        *,
        user_id: str,
        course_ids: list[str] | None = None,
        document_ids: list[str] | None = None,
        top_k: int | None = None,
    ) -> RetrievalOutcome:
        query = query.strip()
        top_k = top_k or self.settings.retrieval_top_k
        query_class = classify_query(query)

        # 1. Access control: the permitted chunk ids, resolved from the database.
        allowed = set(
            self.chunk_repo.scoped_ids(
                user_id, course_ids=course_ids, document_ids=document_ids, level="child"
            )
        )
        if not allowed:
            return RetrievalOutcome(
                chunks=[],
                query=query,
                query_class=query_class,
                sufficient=False,
                reason="There is no indexed material in this scope yet.",
                scope_chunk_count=0,
            )

        fused = self._run_pass(
            query,
            user_id=user_id,
            course_ids=course_ids,
            document_ids=document_ids,
            allowed=allowed,
            query_class=query_class,
        )
        chunks = self._materialise(user_id, fused, top_k)
        sufficient, reason = self._assess(query, chunks)

        reformulated: str | None = None
        if not sufficient:
            # 4. Corrective retrieval: retry against the *same authorised*
            #    material with a keyword-only query before giving up. No web
            #    search, no external source.
            reformulated = self._reformulate(query)
            if reformulated and reformulated != query:
                retry = self._run_pass(
                    reformulated,
                    user_id=user_id,
                    course_ids=course_ids,
                    document_ids=document_ids,
                    allowed=allowed,
                    query_class="general",
                )
                retry_chunks = self._materialise(user_id, retry, top_k)
                retry_sufficient, retry_reason = self._assess(reformulated, retry_chunks)
                if retry_sufficient or len(retry_chunks) > len(chunks):
                    chunks, sufficient, reason = retry_chunks, retry_sufficient, retry_reason

        return RetrievalOutcome(
            chunks=chunks,
            query=query,
            query_class=query_class,
            sufficient=sufficient,
            reason=reason,
            reformulated_query=reformulated,
            scope_chunk_count=len(allowed),
        )

    # --- internals -------------------------------------------------------
    def _run_pass(
        self,
        query: str,
        *,
        user_id: str,
        course_ids: list[str] | None,
        document_ids: list[str] | None,
        allowed: set[str],
        query_class: str,
    ) -> list[tuple[str, float, list[str]]]:
        candidates = self.settings.retrieval_candidates

        dense: list[tuple[str, float]] = []
        try:
            vector = self.embedder.embed_query(query)
            dense = [
                (hit.chunk_id, hit.score)
                for hit in self.vector_store.search(
                    vector, limit=candidates, allowed_chunk_ids=allowed
                )
            ]
        except Exception as exc:
            log.warning("dense_retrieval_failed", extra={"reason": str(exc)})

        sparse: list[tuple[str, float]] = []
        try:
            sparse = [
                (hit.chunk_id, hit.score)
                for hit in self.sparse_index.search(
                    self.chunk_repo.session,
                    query,
                    user_id=user_id,
                    limit=candidates,
                    course_ids=course_ids,
                    document_ids=document_ids,
                    allowed_chunk_ids=allowed,
                )
            ]
        except Exception as exc:
            log.warning("sparse_retrieval_failed", extra={"reason": str(exc)})

        fused = reciprocal_rank_fusion(
            {"dense": dense, "sparse": sparse},
            weights=_WEIGHTS.get(query_class, _WEIGHTS["general"]),
        )
        return [(hit.chunk_id, hit.score, hit.retrievers) for hit in fused]

    def _materialise(
        self, user_id: str, fused: list[tuple[str, float, list[str]]], top_k: int
    ) -> list[RetrievedChunk]:
        if not fused:
            return []
        top = fused[:top_k]
        rows = {
            chunk.id: chunk
            for chunk in self.chunk_repo.get_many(user_id, [chunk_id for chunk_id, _, _ in top])
        }
        parent_ids = [
            rows[chunk_id].parent_id
            for chunk_id, _, _ in top
            if chunk_id in rows and rows[chunk_id].parent_id
        ]
        parents: dict[str, Chunk] = {
            chunk.id: chunk
            for chunk in self.chunk_repo.get_many(user_id, [pid for pid in parent_ids if pid])
        }

        titles: dict[str, str] = {}
        result: list[RetrievedChunk] = []
        for chunk_id, score, retrievers in top:
            chunk = rows.get(chunk_id)
            if chunk is None:
                continue
            if chunk.document_id not in titles:
                try:
                    titles[chunk.document_id] = self.document_repo.get(
                        user_id, chunk.document_id
                    ).title
                except Exception:
                    titles[chunk.document_id] = ""
            parent = parents.get(chunk.parent_id or "")
            result.append(
                RetrievedChunk(
                    chunk_id=chunk.id,
                    text=chunk.text,
                    page_number=chunk.page_number,
                    document_id=chunk.document_id,
                    document_title=titles.get(chunk.document_id, ""),
                    section=chunk.section,
                    topic_slug=chunk.topic_slug,
                    element_type=chunk.element_type,
                    bbox=chunk.bbox,
                    score=score,
                    retrievers=retrievers,
                    parent_text=parent.text if parent else chunk.text,
                )
            )
        return result

    def _assess(self, query: str, chunks: list[RetrievedChunk]) -> tuple[bool, str]:
        """Decide whether the retrieved evidence actually addresses the question.

        Uses retrieval signal only — never a model's own confidence, which is
        not a measure of correctness.

        The primary test is *term coverage*: how many of the question's
        content-bearing words appear anywhere in the retrieved passages. Rank
        scores alone are not usable for this, because RRF scores are scale-free
        — the top result of a hopeless search scores about the same as the top
        result of a perfect one. Coverage is what distinguishes "my slides
        discuss this" from "my slides merely contain the word 'the'".
        """
        if not chunks:
            return False, "Nothing in your material matched this question."

        terms = content_terms(query)
        coverage = self._term_coverage(terms, chunks)
        if terms and coverage < self.settings.sufficiency_min_coverage:
            missing = [term for term in terms if not self._term_present(term, chunks)]
            detail = f" No mention of: {', '.join(missing[:4])}." if missing else ""
            return False, ("Your course material does not appear to cover this question." + detail)

        strong = [c for c in chunks if c.score >= self.settings.sufficiency_min_score]
        if len(strong) < self.settings.sufficiency_min_chunks:
            return False, (
                "Only weak matches were found in your material, so the answer below "
                "may be incomplete."
            )
        if not any(len(c.retrievers) > 1 for c in chunks[:4]):
            return False, (
                "Only one of the two search methods found this, so the match is less certain."
            )
        return True, ""

    @staticmethod
    def _term_present(term: str, chunks: list[RetrievedChunk]) -> bool:
        """Whether a query term appears in the evidence, tolerating inflection."""
        stem = term[:-1] if len(term) > 4 and term.endswith("s") else term
        return any(stem in chunk.text.lower() for chunk in chunks)

    def _term_coverage(self, terms: list[str], chunks: list[RetrievedChunk]) -> float:
        if not terms:
            return 1.0
        top = chunks[: max(4, self.settings.sufficiency_min_chunks)]
        present = sum(1 for term in terms if self._term_present(term, top))
        return present / len(terms)

    @staticmethod
    def _reformulate(query: str) -> str:
        """Strip question framing down to content terms.

        Deterministic: no model call, so corrective retrieval costs nothing and
        behaves identically offline.
        """
        return " ".join(content_terms(query))
