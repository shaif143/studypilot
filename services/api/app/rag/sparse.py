"""Sparse (lexical) retrieval.

Lexical matching matters more here than in a general chatbot: students ask
about named concepts ("what is stride"), and exact term matching is what finds
the slide that *defines* the term.

Three implementations behind one interface, chosen by database dialect:

``SqliteFtsIndex``
    SQLite FTS5 with BM25 ranking. The desktop default — in-process, no extra
    dependency.
``PostgresTextIndex``
    ``tsvector`` with a GIN index and ``ts_rank_cd``. The hosted default.
``PythonBm25Index``
    BM25 computed in Python. A correctness fallback for a SQLite build without
    FTS5; fine for a single student's corpus, too slow to rely on otherwise.

Every method takes the caller's :class:`~sqlalchemy.orm.Session` and runs
through it rather than opening a second connection. SQLite allows only one
writer at a time, so a separate connection deadlocks against the request's own
open write transaction — and because index failures are non-fatal by design, it
does so *silently*, leaving documents indexed for dense search only.
"""

from __future__ import annotations

import logging
import math
import re
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.storage.db import Database

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
#: FTS5 and tsquery both treat punctuation as syntax; strip it from user input.
_FTS_SPECIAL = re.compile(r"[^A-Za-z0-9\s]")

#: Function words and question framing. BM25's IDF already discounts these, but
#: an OR-query built from them still *matches* almost every chunk, which made
#: every question look answerable. They are removed from the query instead.
STOPWORDS = frozenset(
    # Written as prose and split at import: a word list is far easier to read
    # and amend this way than as a few hundred quoted strings.
    """a about above after again against all also am an and any are as at be because been
    before being below between both but by can cannot could did do does doing down during
    each few for from further had has have having he her here hers him his how i if in into
    is it its itself just me more most my no nor not of off on once only or other ought our
    out over own same she should so some such than that the their them then there these they
    this those through to too under until up very was we were what when where which while
    who whom why will with would you your explain describe define tell give please mean
    means example show shows let us need want know understand possibly earth""".split()  # noqa: SIM905
)


def tokenize(value: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(value)]


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


class SparseIndex(ABC):
    """Lexical index over chunk text, always scoped to one user."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @property
    @abstractmethod
    def backend(self) -> str: ...

    @abstractmethod
    def index_chunks(self, session: Session, rows: list[dict[str, str]]) -> None:
        """Add chunks. Each row carries chunk_id, user_id, course_id, document_id, text."""

    @abstractmethod
    def delete_for_document(self, session: Session, document_id: str) -> None: ...

    @abstractmethod
    def delete_for_user(self, session: Session, user_id: str) -> None: ...

    @abstractmethod
    def _search(
        self,
        session: Session,
        tokens: list[str],
        user_id: str,
        limit: int,
        course_ids: list[str] | None,
        document_ids: list[str] | None,
    ) -> list[SparseHit]: ...

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
        hits = self._search(session, tokens, user_id, limit, course_ids, document_ids)
        return [
            hit for hit in hits if allowed_chunk_ids is None or hit.chunk_id in allowed_chunk_ids
        ]

    # --- shared SQL helpers ---------------------------------------------
    @staticmethod
    def _scope_clauses(
        course_ids: list[str] | None, document_ids: list[str] | None
    ) -> tuple[list[str], dict[str, object]]:
        """Build the course/document filters shared by every backend."""
        clauses: list[str] = []
        params: dict[str, object] = {}
        if course_ids:
            placeholders = ", ".join(f":course_{i}" for i in range(len(course_ids)))
            clauses.append(f"course_id IN ({placeholders})")
            params.update({f"course_{i}": value for i, value in enumerate(course_ids)})
        if document_ids:
            placeholders = ", ".join(f":doc_{i}" for i in range(len(document_ids)))
            clauses.append(f"document_id IN ({placeholders})")
            params.update({f"doc_{i}": value for i, value in enumerate(document_ids)})
        return clauses, params

    @staticmethod
    def _safe_terms(tokens: list[str]) -> list[str]:
        """Strip anything the query language would treat as syntax."""
        return [cleaned for token in tokens if (cleaned := _FTS_SPECIAL.sub("", token))]


class SqliteFtsIndex(SparseIndex):
    """SQLite FTS5 with BM25 ranking."""

    @property
    def backend(self) -> str:
        return "fts5"

    def index_chunks(self, session: Session, rows: list[dict[str, str]]) -> None:
        if not rows:
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
        session.execute(
            sql_text("DELETE FROM chunk_fts WHERE document_id = :document_id"),
            {"document_id": document_id},
        )
        session.flush()

    def delete_for_user(self, session: Session, user_id: str) -> None:
        session.execute(
            sql_text("DELETE FROM chunk_fts WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
        session.flush()

    def _search(
        self,
        session: Session,
        tokens: list[str],
        user_id: str,
        limit: int,
        course_ids: list[str] | None,
        document_ids: list[str] | None,
    ) -> list[SparseHit]:
        # Quote each term so FTS5 syntax in the student's question is inert.
        safe = [f'"{term}"' for term in self._safe_terms(tokens)]
        if not safe:
            return []

        clauses, params = self._scope_clauses(course_ids, document_ids)
        params |= {"match": " OR ".join(safe), "user_id": user_id, "limit": limit}
        where = " AND ".join(["chunk_fts MATCH :match", "user_id = :user_id", *clauses])

        try:
            rows = session.execute(
                sql_text(
                    "SELECT chunk_id, bm25(chunk_fts) AS rank FROM chunk_fts "
                    f"WHERE {where} ORDER BY rank LIMIT :limit"
                ),
                params,
            ).all()
        except Exception as exc:
            log.warning("fts_query_failed", extra={"reason": str(exc)})
            return []
        # bm25() returns a negative number where more negative is a better match.
        return [SparseHit(chunk_id=str(row[0]), score=-float(row[1])) for row in rows]


class PostgresTextIndex(SparseIndex):
    """PostgreSQL ``tsvector`` search with a GIN index.

    ``ts_rank_cd`` is used rather than ``ts_rank`` because it accounts for term
    proximity, which matters when a slide mentions several query terms in one
    sentence rather than scattered across the page.
    """

    @property
    def backend(self) -> str:
        return "postgres-tsvector"

    def index_chunks(self, session: Session, rows: list[dict[str, str]]) -> None:
        if not rows:
            return
        session.execute(
            sql_text(
                "INSERT INTO chunk_search (chunk_id, user_id, course_id, document_id, document) "
                "VALUES (:chunk_id, :user_id, :course_id, :document_id, "
                "        to_tsvector('english', :text)) "
                "ON CONFLICT (chunk_id) DO UPDATE SET document = EXCLUDED.document"
            ),
            rows,
        )
        session.flush()

    def delete_for_document(self, session: Session, document_id: str) -> None:
        session.execute(
            sql_text("DELETE FROM chunk_search WHERE document_id = :document_id"),
            {"document_id": document_id},
        )
        session.flush()

    def delete_for_user(self, session: Session, user_id: str) -> None:
        session.execute(
            sql_text("DELETE FROM chunk_search WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
        session.flush()

    def _search(
        self,
        session: Session,
        tokens: list[str],
        user_id: str,
        limit: int,
        course_ids: list[str] | None,
        document_ids: list[str] | None,
    ) -> list[SparseHit]:
        safe = self._safe_terms(tokens)
        if not safe:
            return []

        clauses, params = self._scope_clauses(course_ids, document_ids)
        # The terms are already stripped to [A-Za-z0-9]; joining with | gives an
        # OR query equivalent to the FTS5 path.
        params |= {"query": " | ".join(safe), "user_id": user_id, "limit": limit}
        where = " AND ".join(
            ["document @@ to_tsquery('english', :query)", "user_id = :user_id", *clauses]
        )

        try:
            rows = session.execute(
                sql_text(
                    "SELECT chunk_id, ts_rank_cd(document, to_tsquery('english', :query)) AS rank "
                    f"FROM chunk_search WHERE {where} ORDER BY rank DESC LIMIT :limit"
                ),
                params,
            ).all()
        except Exception as exc:
            log.warning("tsquery_failed", extra={"reason": str(exc)})
            return []
        return [SparseHit(chunk_id=str(row[0]), score=float(row[1])) for row in rows]


class PythonBm25Index(SparseIndex):
    """BM25 computed in Python, for a SQLite build without FTS5."""

    @property
    def backend(self) -> str:
        return "python-bm25"

    def index_chunks(self, session: Session, rows: list[dict[str, str]]) -> None:
        # Nothing to build: this backend reads the `chunks` table directly.
        return

    def delete_for_document(self, session: Session, document_id: str) -> None:
        return

    def delete_for_user(self, session: Session, user_id: str) -> None:
        return

    def _search(
        self,
        session: Session,
        tokens: list[str],
        user_id: str,
        limit: int,
        course_ids: list[str] | None,
        document_ids: list[str] | None,
    ) -> list[SparseHit]:
        clauses, params = self._scope_clauses(course_ids, document_ids)
        params["user_id"] = user_id
        where = " AND ".join(["user_id = :user_id", "level = 'child'", *clauses])

        rows = session.execute(sql_text(f"SELECT id, text FROM chunks WHERE {where}"), params).all()
        if not rows:
            return []

        documents = [(str(row[0]), tokenize(str(row[1]))) for row in rows]
        total = len(documents)
        average_length = sum(len(terms) for _, terms in documents) / total
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
                denominator = frequency + k1 * (1 - b + b * length / average_length)
                score += idf * frequency * (k1 + 1) / denominator
            if score > 0:
                scored.append(SparseHit(chunk_id=chunk_id, score=score))
        scored.sort(key=lambda hit: hit.score, reverse=True)
        return scored[:limit]


def build_sparse_index(database: Database) -> SparseIndex:
    """Pick the lexical backend that matches the database in use."""
    if database.dialect == "postgresql":
        return PostgresTextIndex(database)
    if database.supports_fts:
        return SqliteFtsIndex(database)
    log.warning(
        "fts_unavailable",
        extra={"detail": "falling back to in-process BM25; expect slower search"},
    )
    return PythonBm25Index(database)
