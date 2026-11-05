"""Which database the configuration actually selects.

This exists because of a bug that would have reached production: a patch to
``Database.from_settings`` silently failed to apply, so
``STUDYPILOT_DATABASE_URL`` was ignored and every hosted deployment would have
used an ephemeral SQLite file instead of PostgreSQL.

It was invisible from the outside. The service started, served requests and
reported itself healthy — and the durability warning that exists to catch
exactly this stayed silent, because the *setting* was present; only the code
that reads it was not. Nothing failed until a redeploy wiped the disk.

These tests assert the wiring between configuration and engine directly, so
the next silent failure is a red test rather than lost documents.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.runtime import RuntimeContext
from app.storage.db import Database, normalise_database_url


def test_from_settings_defaults_to_local_sqlite(tmp_path):
    database = Database.from_settings(Settings(data_dir=tmp_path))
    assert database.dialect == "sqlite"
    assert str(tmp_path) in database.url
    database.dispose()


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://user:pw@db.example.com:5432/studypilot",
        # The bare alias managed providers still hand out.
        "postgres://user:pw@db.example.com:5432/studypilot",
        # Already driver-qualified.
        "postgresql+psycopg://user:pw@db.example.com:5432/studypilot",
    ],
)
def test_from_settings_honours_the_configured_database(tmp_path, dsn: str):
    """The regression test for the bug described above."""
    database = Database.from_settings(Settings(data_dir=tmp_path, database_url=dsn))

    assert database.dialect == "postgresql", (
        "STUDYPILOT_DATABASE_URL was ignored — the deployment would silently use ephemeral SQLite"
    )
    assert "sqlite" not in database.url
    assert database.url.startswith("postgresql+psycopg://")
    database.dispose()


def test_configured_database_reaches_the_runtime_context(tmp_path, monkeypatch):
    """The setting must survive the whole way to the objects that use it.

    `from_settings` being correct is not enough: the vector store is chosen
    from the same database object, and pgvector refuses a non-PostgreSQL one.
    """
    captured: dict[str, object] = {}

    real_from_settings = Database.from_settings

    @classmethod
    def spy(cls, settings):
        database = real_from_settings(settings)
        captured["dialect"] = database.dialect
        captured["url"] = database.url
        return database

    monkeypatch.setattr(Database, "from_settings", spy)

    settings = Settings(
        data_dir=tmp_path,
        deployment_mode="hosted",
        jwt_secret="s" * 40,
        database_url="postgresql://user:pw@db.example.com:5432/studypilot",
        vector_store="pgvector",
    )
    # Creating the context fails at connection time — there is no server at
    # db.example.com — but only *after* the engine has been built from the
    # configured URL, which is what this asserts.
    with pytest.raises(OperationalError):
        RuntimeContext.create(settings)

    assert captured.get("dialect") == "postgresql"
    assert "sqlite" not in str(captured.get("url", ""))


def test_pgvector_is_refused_on_sqlite(tmp_path):
    """A mismatch must fail loudly rather than quietly return no results."""
    from app.rag.vector_store import build_vector_store

    sqlite_database = Database.from_settings(Settings(data_dir=tmp_path))
    with pytest.raises(ValueError, match="PostgreSQL"):
        build_vector_store("pgvector", tmp_path, database=sqlite_database)
    sqlite_database.dispose()


def test_normalisation_is_idempotent():
    once = normalise_database_url("postgres://u:p@remote.example/db")
    assert normalise_database_url(once) == once


def test_a_quoted_dsn_is_accepted():
    """`neon env pull` quotes the DSN, and the quotes survive a copy-paste.

    Pasted into a hosting platform's environment form the quotes become part
    of the value, no scheme matches, and the service fails to start with a
    message about an unrecognised dialect that mentions nothing about quotes.
    """
    bare = normalise_database_url("postgresql://u:p@remote.example/db")
    for quoted in (
        '"postgresql://u:p@remote.example/db"',
        "'postgresql://u:p@remote.example/db'",
        '  "postgresql://u:p@remote.example/db"  ',
    ):
        assert normalise_database_url(quoted) == bare
    assert bare.startswith("postgresql+psycopg://")
    assert "sslmode=require" in bare
