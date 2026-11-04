"""Database engine and session management.

SQLite is the desktop target; the engine URL is the only place that needs to
change for a hosted PostgreSQL deployment.  SQLite requires explicit pragmas
(WAL, foreign keys) which are applied per connection.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.storage.models import Base

log = logging.getLogger(__name__)


class Database:
    """Owns the engine and exposes sessions.

    A single instance is created per process and stored on the FastAPI app
    state, so tests can construct an isolated database without global state.
    """

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.url = url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(url, echo=echo, future=True, connect_args=connect_args)
        if url.startswith("sqlite"):
            self._install_sqlite_pragmas()
        self._session_factory = sessionmaker(
            bind=self.engine, expire_on_commit=False, class_=Session
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> Database:
        settings.ensure_dirs()
        return cls(f"sqlite+pysqlite:///{settings.db_path}")

    @classmethod
    def in_memory(cls) -> Database:
        return cls("sqlite+pysqlite:///:memory:")

    def _install_sqlite_pragmas(self) -> None:
        @event.listens_for(self.engine, "connect")
        def _set_pragmas(dbapi_connection, _record):  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        self._create_fts()

    def _create_fts(self) -> None:
        """Create the FTS5 index used for BM25 sparse retrieval.

        Kept here (rather than in the ORM) because it is a SQLite-specific
        physical structure.  A PostgreSQL deployment would use tsvector instead;
        both sit behind :class:`app.rag.sparse.SparseIndex`.
        """
        if not self.url.startswith("sqlite"):
            return
        with self.engine.begin() as conn:
            try:
                conn.execute(
                    text(
                        "CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5("
                        "  chunk_id UNINDEXED,"
                        "  user_id UNINDEXED,"
                        "  course_id UNINDEXED,"
                        "  document_id UNINDEXED,"
                        "  text,"
                        "  tokenize='porter unicode61'"
                        ")"
                    )
                )
            except Exception:  # pragma: no cover - FTS5 absent in a custom build
                log.warning("fts5_unavailable", extra={"detail": "sparse retrieval disabled"})

    @property
    def supports_fts(self) -> bool:
        if not self.url.startswith("sqlite"):
            return False
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT name FROM sqlite_master WHERE name='chunk_fts'")
            ).first()
            return row is not None

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
