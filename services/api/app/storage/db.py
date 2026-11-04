"""Database engine and session management.

SQLite is the desktop target; the engine URL is the only place that needs to
change for a hosted PostgreSQL deployment.  SQLite requires explicit pragmas
(WAL, foreign keys) which are applied per connection.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urlsplit

from sqlalchemy import Engine, create_engine, event, inspect, text
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
        # "sqlite" | "postgresql" | ... — drives the dialect-specific pieces
        # (pragmas, full-text structures) without scattering URL parsing about.
        self.dialect = url.split(":", 1)[0].split("+", 1)[0]
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
        self._add_missing_columns()
        self._create_search_structures()

    def _add_missing_columns(self) -> None:
        """Add columns the ORM knows about but an existing table lacks.

        ``create_all`` creates missing *tables* but never alters existing ones,
        so a desktop install that predates a new column would keep failing
        until its database was deleted. This covers the only migration shape
        this project has needed so far — adding a nullable column with a
        default — and anything more involved should bring in Alembic rather
        than grow this.
        """
        inspector = inspect(self.engine)
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                if not inspector.has_table(table.name):
                    continue
                existing = {column["name"] for column in inspector.get_columns(table.name)}
                for column in table.columns:
                    if column.name in existing:
                        continue
                    if not column.nullable and column.default is None:
                        log.warning(
                            "cannot_add_column",
                            extra={
                                "table": table.name,
                                "column": column.name,
                                "reason": "not nullable and has no default",
                            },
                        )
                        continue
                    ddl = column.type.compile(self.engine.dialect)
                    statement = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {ddl}"
                    if column.default is not None and column.default.is_scalar:
                        value = column.default.arg
                        literal = (
                            "NULL"
                            if value is None
                            else str(int(value))
                            if isinstance(value, bool)
                            else repr(value)
                            if isinstance(value, str)
                            else str(value)
                        )
                        statement += f" DEFAULT {literal}"
                    log.info("adding_column", extra={"table": table.name, "column": column.name})
                    conn.execute(text(statement))

    def _create_search_structures(self) -> None:
        """Create the dialect-specific full-text structures.

        Kept out of the ORM because these are physical structures with no
        portable spelling: SQLite gets an FTS5 virtual table, PostgreSQL a
        tsvector table with a GIN index. Both sit behind
        :class:`app.rag.sparse.SparseIndex`.
        """
        if self.dialect == "sqlite":
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
                    log.warning("fts5_unavailable", extra={"detail": "falling back to BM25"})
            return

        if self.dialect == "postgresql":
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        "CREATE TABLE IF NOT EXISTS chunk_search ("
                        "  chunk_id TEXT PRIMARY KEY,"
                        "  user_id TEXT NOT NULL,"
                        "  course_id TEXT NOT NULL,"
                        "  document_id TEXT NOT NULL,"
                        "  document tsvector NOT NULL"
                        ")"
                    )
                )
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_chunk_search_document "
                        "ON chunk_search USING GIN (document)"
                    )
                )
                # Scope filters run alongside the text match on every query.
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_chunk_search_scope "
                        "ON chunk_search (user_id, course_id, document_id)"
                    )
                )

    @property
    def supports_fts(self) -> bool:
        """Whether the SQLite FTS5 virtual table exists."""
        if self.dialect != "sqlite":
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


def normalise_database_url(url: str) -> str:
    """Make a provider-supplied DSN usable by SQLAlchemy.

    Two corrections, both of which a managed provider's copy-paste string
    usually needs:

    * ``postgres://`` is rewritten to the driver-qualified form SQLAlchemy
      expects, since it dropped the bare alias.
    * TLS is required for a *remote* host. It is not forced on a loopback
      connection, because a local development database generally has no
      certificate and would refuse the connection outright.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]

    if url.startswith("postgresql+psycopg://") and "sslmode=" not in url:
        host = urlsplit(url).hostname or ""
        is_local = host in {"localhost", "127.0.0.1", "::1", ""} or host.endswith(".local")
        if not is_local:
            url += ("&" if "?" in url else "?") + "sslmode=require"
    return url
