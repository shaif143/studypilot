"""Sparse (lexical) retrieval.

SQLite FTS5 provides BM25 ranking in-process with no extra dependency, and it
sits behind this interface so a PostgreSQL deployment can swap in tsvector
ranking.  A pure-Python fallback keeps lexical retrieval working if FTS5 is
missing from the SQLite build.

Lexical matching matters more here than in a general chatbot: students ask
about named concepts ("what is stride"), and exact term matching is what finds
the slide that defines the term.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.storage.db import Database

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
#: FTS5 treats these as syntax; they must be stripped from user input.
_FTS_SPECIAL = re.compile(r"[^A-Za-z0-9\s]")

#: Function words and question framing. BM25's IDF already discounts these, but
#: an OR-query built from them still *matches* almost every chunk, which made
#: every question look answerable. They are removed from the query instead.
STOPWORDS = frozenset(
    [
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "also",
        "am",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "cannot",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "him",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "itself",
        "just",
        "me",
        "more",
        "most",
        "my",
        "no",
        "nor",
        "not",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "ought",
        "our",
        "out",
        "over",
        "own",
        "same",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "explain",
        "describe",
        "define",
        "tell",
        "give",
        "please",
        "mean",
        "means",
        "example",
        "show",
        "shows",
        "let",
        "us",
        "need",
        "want",
        "know",
        "understand",
        "possibly",
        "earth",
    ]
)


def content_terms(value: str) -> list[str]:
    """Query terms that carry topic meaning, in first-seen order."""
    seen: dict[str, None] = {}
    for token in _TOKEN_RE.findall(value.lower()):
        if len(token) > 2 and token not in STOPWORDS:
            seen.setdefault(token, None)
    return list(seen)


@dataclass
class SparseHit:
    chunk_id: str
    score: float


def tokenize(value: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(value)]


class SparseIndex:
    """BM25 search over chunk text, scoped to one user.

    Every method takes the caller's :class:`~sqlalchemy.orm.Session` and runs
    through it rather than opening a second connection.  SQLite allows only one
    writer at a time, so a separate connection would deadlock against the
    request's own open write transaction — and, because index failures are
    non-fatal by design, it would do so *silently*, leaving documents indexed
    for dense search only.
    """

    def __init__(self, database: Database) -> None:
        self.database = database
        self.use_fts = database.supports_fts

    # --- writes ----------------------------------------------------------
    def index_chunks(self, session: Session, rows: list[dict[str, str]]) -> None:
        """Add chunks. ``rows`` carries chunk_id, user_id, course_id, document_id, text."""
        if not rows or not self.use_fts:
            return
        session.execute(
            sql_text(
                "INSERT INTO chunk_fts (chunk_id, user_id, course_id, document_id, text) "
                "VALUES (:chunk_id, :user_id, :course_id, :document_id, :text)"
            ),
            rows,
        )
        session.flush()

    def delete_for_document(self, session: Session, document_id: str) -> None:
        if not self.use_fts:
            return
        session.execute(
            sql_text("DELETE FROM chunk_fts WHERE document_id = :document_id"),
            {"document_id": document_id},
        )
        session.flush()

    def delete_for_user(self, session: Session, user_id: str) -> None:
        if not self.use_fts:
            return
        session.execute(
            sql_text("DELETE FROM chunk_fts WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
        session.flush()

    # --- reads -----------------------------------------------------------
    def search(
        self,
        session: Session,
        query: str,
        *,
        user_id: str,
        limit: int,
        course_ids: list[str] | None = None,
        document_ids: list[str] | None = None,
        allowed_chunk_ids: set[str] | None = None,
    ) -> list[SparseHit]:
        tokens = content_terms(query) or tokenize(query)
        if not tokens:
            return []
        if self.use_fts:
            hits = self._search_fts(session, tokens, user_id, limit, course_ids, document_ids)
            return [
                hit
                for hit in hits
                if allowed_chunk_ids is None or hit.chunk_id in allowed_chunk_ids
            ]
        hits = self._search_python(session, tokens, user_id, limit, course_ids, document_ids)
        return [
            hit for hit in hits if allowed_chunk_ids is None or hit.chunk_id in allowed_chunk_ids
        ]

    def _search_fts(
        self,
        session: Session,
        tokens: list[str],
        user_id: str,
        limit: int,
        course_ids: list[str] | None,
        document_ids: list[str] | None,
    ) -> list[SparseHit]:
        # Quote each token so FTS5 syntax in the student's question is inert.
        safe = [f'"{_FTS_SPECIAL.sub("", token)}"' for token in tokens if token]
        if not safe:
            return []
        match_expr = " OR ".join(safe)

        clauses = ["chunk_fts MATCH :match", "user_id = :user_id"]
        params: dict[str, object] = {"match": match_expr, "user_id": user_id, "limit": limit}
        if course_ids:
            placeholders = ", ".join(f":course_{i}" for i in range(len(course_ids)))
            clauses.append(f"course_id IN ({placeholders})")
            params.update({f"course_{i}": value for i, value in enumerate(course_ids)})
        if document_ids:
            placeholders = ", ".join(f":doc_{i}" for i in range(len(document_ids)))
            clauses.append(f"document_id IN ({placeholders})")
            params.update({f"doc_{i}": value for i, value in enumerate(document_ids)})

        statement = (
            "SELECT chunk_id, bm25(chunk_fts) AS rank FROM chunk_fts "
            f"WHERE {' AND '.join(clauses)} ORDER BY rank LIMIT :limit"
        )
        try:
            rows = session.execute(sql_text(statement), params).all()
        except Exception as exc:
            log.warning("fts_query_failed", extra={"reason": str(exc)})
            return []
        # bm25() returns a negative number where more negative is a better match.
        return [SparseHit(chunk_id=str(row[0]), score=-float(row[1])) for row in rows]

    def _search_python(
        self,
        session: Session,
        tokens: list[str],
        user_id: str,
        limit: int,
        course_ids: list[str] | None,
        document_ids: list[str] | None,
    ) -> list[SparseHit]:
        """BM25 computed in Python when FTS5 is unavailable."""
        clauses = ["user_id = :user_id", "level = 'child'"]
        params: dict[str, object] = {"user_id": user_id}
        if course_ids:
            placeholders = ", ".join(f":course_{i}" for i in range(len(course_ids)))
            clauses.append(f"course_id IN ({placeholders})")
            params.update({f"course_{i}": value for i, value in enumerate(course_ids)})
        if document_ids:
            placeholders = ", ".join(f":doc_{i}" for i in range(len(document_ids)))
            clauses.append(f"document_id IN ({placeholders})")
            params.update({f"doc_{i}": value for i, value in enumerate(document_ids)})

        rows = session.execute(
            sql_text(f"SELECT id, text FROM chunks WHERE {' AND '.join(clauses)}"), params
        ).all()
        if not rows:
            return []

        documents = [(str(row[0]), tokenize(str(row[1]))) for row in rows]
        total = len(documents)
        avg_length = sum(len(terms) for _, terms in documents) / total
        frequencies: Counter[str] = Counter()
        for _, terms in documents:
            frequencies.update(set(terms))

        k1, b = 1.5, 0.75
        scored: list[SparseHit] = []
        for chunk_id, terms in documents:
            counts = Counter(terms)
            length = len(terms) or 1
            score = 0.0
            for token in tokens:
                frequency = counts.get(token, 0)
                if frequency == 0:
                    continue
                containing = frequencies[token]
                idf = math.log(1 + (total - containing + 0.5) / (containing + 0.5))
                denominator = frequency + k1 * (1 - b + b * length / avg_length)
                score += idf * frequency * (k1 + 1) / denominator
            if score > 0:
                scored.append(SparseHit(chunk_id=chunk_id, score=score))
        scored.sort(key=lambda hit: hit.score, reverse=True)
        return scored[:limit]
