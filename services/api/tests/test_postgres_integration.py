"""The hosted data path against a real PostgreSQL database.

The desktop suite exercises SQLite and the in-process vector store. Those are
different implementations from the ones a deployment runs, so passing there
says nothing about whether `tsvector` search or pgvector similarity actually
work. This module runs the same guarantees against the real thing.

Skipped unless a database is supplied:

    docker run -d --name studypilot-pg -e POSTGRES_PASSWORD=studypilot \\
      -e POSTGRES_DB=studypilot -p 55432:5432 pgvector/pgvector:pg17

    STUDYPILOT_TEST_DATABASE_URL=postgresql://postgres:studypilot@127.0.0.1:55432/studypilot \\
      npm run api:test
"""

from __future__ import annotations

import os
import uuid

import pytest
from conftest import HOSTED_SECRET
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models.embeddings import HashingEmbedder
from app.rag.sparse import PostgresTextIndex, build_sparse_index
from app.rag.vector_store import PgVectorStore, VectorRecord, build_vector_store
from app.runtime import RuntimeContext
from app.storage.db import Database, normalise_database_url
from app.storage.files import LocalBlobStore

DATABASE_URL = os.environ.get("STUDYPILOT_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="Set STUDYPILOT_TEST_DATABASE_URL to run the PostgreSQL integration tests.",
)


@pytest.fixture
def pg_database():
    """A database whose tables are dropped afterwards, so runs do not collide."""
    database = Database(normalise_database_url(DATABASE_URL))
    database.create_all()
    yield database
    from sqlalchemy import text

    with database.engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    database.dispose()


@pytest.fixture
def pg_settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "pg",
        deployment_mode="hosted",
        jwt_secret=HOSTED_SECRET,
        database_url=DATABASE_URL,
        vector_store="pgvector",
        embedding_provider="hash",
        llm_provider="extractive",
        log_level="WARNING",
    )


@pytest.fixture
def pg_context(pg_settings: Settings, pg_database: Database):
    pg_settings.ensure_dirs()
    embedder = HashingEmbedder()
    store = build_vector_store("pgvector", pg_settings.vector_dir, database=pg_database)
    store.ensure_collection(embedder.dimensions)
    context = RuntimeContext(
        settings=pg_settings,
        database=pg_database,
        blobs=LocalBlobStore(pg_settings.blob_dir, pg_settings.preview_dir),
        vector_store=store,
        sparse_index=build_sparse_index(pg_database),
        embedder=embedder,
    )
    yield context


@pytest.fixture
def pg_client(pg_settings: Settings, pg_context: RuntimeContext) -> TestClient:
    app = create_app(pg_settings, context=pg_context)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


# --- the backends are the ones we expect ---------------------------------
def test_postgres_selects_the_right_backends(pg_context: RuntimeContext):
    assert pg_context.database.dialect == "postgresql"
    assert isinstance(pg_context.sparse_index, PostgresTextIndex)
    assert pg_context.sparse_index.backend == "postgres-tsvector"
    assert isinstance(pg_context.vector_store, PgVectorStore)
    assert pg_context.vector_store.backend == "pgvector"


def test_url_normalisation_handles_provider_dsns():
    # The bare postgres:// alias SQLAlchemy dropped.
    assert normalise_database_url("postgres://u:p@h/db").startswith("postgresql+psycopg://")
    # TLS required for a remote host...
    assert "sslmode=require" in normalise_database_url("postgres://u:p@remote.example/db")
    # ...but not forced on loopback, where there is usually no certificate and
    # the server rejects the connection outright.
    for local in ("localhost", "127.0.0.1"):
        assert "sslmode" not in normalise_database_url(f"postgres://u:p@{local}:5432/db")
    # An explicit choice is respected rather than duplicated.
    assert normalise_database_url("postgresql://u:p@h/db?sslmode=disable").count("sslmode") == 1


# --- pgvector -------------------------------------------------------------
def test_pgvector_ranks_by_similarity_and_respects_the_allow_list(pg_database: Database):
    embedder = HashingEmbedder()
    store = PgVectorStore(pg_database)
    store.ensure_collection(embedder.dimensions)

    texts = {
        "padding": "Padding adds a border of zeros around the input before convolution.",
        "pooling": "Max pooling takes the largest value in each window.",
        "lisbon": "Lisbon is the capital city of Portugal.",
    }
    store.upsert(
        [
            VectorRecord(
                chunk_id=key,
                vector=embedder.embed_query(value),
                payload={"user_id": "u1", "course_id": "c1", "document_id": "d1"},
            )
            for key, value in texts.items()
        ]
    )
    assert store.count() == 3

    hits = store.search(embedder.embed_query("padding border of zeros"), limit=3)
    assert hits[0].chunk_id == "padding"
    assert hits[0].score > hits[-1].score
    assert hits[0].payload["user_id"] == "u1"

    # The allow-list is the access-control boundary.
    scoped = store.search(
        embedder.embed_query("padding border of zeros"), limit=3, allowed_chunk_ids={"pooling"}
    )
    assert [hit.chunk_id for hit in scoped] == ["pooling"]
    assert store.search(embedder.embed_query("anything"), limit=3, allowed_chunk_ids=set()) == []


def test_pgvector_falls_back_across_providers(pg_database: Database):
    """Neon and standard PostgreSQL offer different extensions and index types.

    Neon ships ``lakebase_vector`` / ``lakebase_ann``; everyone else ships
    ``pgvector`` / ``hnsw``. The query syntax is identical, so only setup has
    to adapt — and it must adapt rather than assume, or the store fails to
    initialise on one of the two.
    """
    from sqlalchemy import text

    store = PgVectorStore(pg_database)
    store.ensure_collection(HashingEmbedder().dimensions)

    # This container is stock pgvector, so the Neon-specific names must have
    # been tried and fallen back from.
    assert store.index_method in {"hnsw", "lakebase_ann"}

    with pg_database.engine.connect() as conn:
        indexes = dict(
            conn.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename='chunk_vectors'")
            ).all()
        )
    assert "ix_chunk_vectors_embedding" in indexes
    assert store.index_method in indexes["ix_chunk_vectors_embedding"]
    # The scope index must exist too: it is what makes the access-control
    # filter cheap rather than a full scan.
    assert "ix_chunk_vectors_scope" in indexes


def test_pgvector_still_works_without_any_index(pg_database: Database):
    """Correctness must not depend on the approximate index, only speed."""
    from sqlalchemy import text

    embedder = HashingEmbedder()
    store = PgVectorStore(pg_database)
    store.ensure_collection(embedder.dimensions)
    store.upsert(
        [
            VectorRecord(
                chunk_id="padding",
                vector=embedder.embed_query("Padding adds a border of zeros."),
                payload={},
            ),
            VectorRecord(
                chunk_id="lisbon",
                vector=embedder.embed_query("Lisbon is the capital of Portugal."),
                payload={},
            ),
        ]
    )
    with pg_database.engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS ix_chunk_vectors_embedding"))

    hits = store.search(embedder.embed_query("padding border zeros"), limit=2)
    assert hits[0].chunk_id == "padding"


def test_pgvector_upsert_is_idempotent(pg_database: Database):
    embedder = HashingEmbedder()
    store = PgVectorStore(pg_database)
    store.ensure_collection(embedder.dimensions)
    record = VectorRecord(
        chunk_id="same", vector=embedder.embed_query("first"), payload={"user_id": "u"}
    )
    store.upsert([record])
    store.upsert([record])
    assert store.count() == 1


def test_pgvector_delete_and_reset(pg_database: Database):
    embedder = HashingEmbedder()
    store = PgVectorStore(pg_database)
    store.ensure_collection(embedder.dimensions)
    store.upsert(
        [
            VectorRecord(chunk_id=f"c{i}", vector=embedder.embed_query(f"text {i}"), payload={})
            for i in range(5)
        ]
    )
    store.delete(["c0", "c1"])
    assert store.count() == 3
    store.reset()
    assert store.count() == 0


def test_changing_the_embedding_dimension_rebuilds_the_table(pg_database: Database):
    """Vectors of a different width are meaningless, not merely incompatible."""
    store = PgVectorStore(pg_database)
    store.ensure_collection(384)
    store.upsert([VectorRecord(chunk_id="old", vector=[0.1] * 384, payload={})])
    assert store.count() == 1

    store.ensure_collection(256)
    assert store.count() == 0
    store.upsert([VectorRecord(chunk_id="new", vector=[0.1] * 256, payload={})])
    assert store.count() == 1


# --- tsvector sparse search ------------------------------------------------
def test_postgres_sparse_search_finds_and_scopes(pg_database: Database):
    index = PostgresTextIndex(pg_database)
    rows = [
        {
            "chunk_id": "c1",
            "user_id": "u1",
            "course_id": "course-1",
            "document_id": "doc-1",
            "text": "Padding is necessary because without it the feature map shrinks.",
        },
        {
            "chunk_id": "c2",
            "user_id": "u1",
            "course_id": "course-1",
            "document_id": "doc-2",
            "text": "Stride controls how far the kernel moves between applications.",
        },
        {
            "chunk_id": "c3",
            "user_id": "u2",
            "course_id": "course-9",
            "document_id": "doc-9",
            "text": "Padding in another student's private lecture notes.",
        },
    ]
    with pg_database.session() as session:
        index.index_chunks(session, rows)

    with pg_database.session() as session:
        hits = index.search(session, "Why is padding necessary?", user_id="u1", limit=5)
        assert [hit.chunk_id for hit in hits] == ["c1"]
        assert hits[0].score > 0

        # Another user's matching chunk must never be returned.
        assert index.search(session, "padding", user_id="u1", limit=5)[0].chunk_id == "c1"
        assert [h.chunk_id for h in index.search(session, "padding", user_id="u2", limit=5)] == [
            "c3"
        ]

        # Scope filters.
        assert index.search(session, "padding", user_id="u1", limit=5, document_ids=["doc-2"]) == []
        assert (
            index.search(session, "stride", user_id="u1", limit=5, course_ids=["course-1"])[
                0
            ].chunk_id
            == "c2"
        )


def test_postgres_sparse_survives_tsquery_syntax_in_a_question(pg_database: Database):
    """Operators a student types must be inert, not a syntax error."""
    index = PostgresTextIndex(pg_database)
    with pg_database.session() as session:
        index.index_chunks(
            session,
            [
                {
                    "chunk_id": "c1",
                    "user_id": "u1",
                    "course_id": "c",
                    "document_id": "d",
                    "text": "Padding adds a border.",
                }
            ],
        )
    with pg_database.session() as session:
        for hostile in ("padding & | ! ( )", "padding <-> border", "'; DROP TABLE chunks; --"):
            assert isinstance(index.search(session, hostile, user_id="u1", limit=5), list)
        # The table is still there.
        assert index.search(session, "padding", user_id="u1", limit=5)[0].chunk_id == "c1"


def test_postgres_sparse_deletes(pg_database: Database):
    index = PostgresTextIndex(pg_database)
    rows = [
        {
            "chunk_id": f"c{i}",
            "user_id": "u1",
            "course_id": "course-1",
            "document_id": "doc-1" if i < 2 else "doc-2",
            "text": "padding and stride",
        }
        for i in range(4)
    ]
    with pg_database.session() as session:
        index.index_chunks(session, rows)
    with pg_database.session() as session:
        index.delete_for_document(session, "doc-1")
    with pg_database.session() as session:
        assert len(index.search(session, "padding", user_id="u1", limit=10)) == 2
        index.delete_for_user(session, "u1")
    with pg_database.session() as session:
        assert index.search(session, "padding", user_id="u1", limit=10) == []


# --- the whole workflow, on Postgres --------------------------------------
def test_full_workflow_on_postgres(pg_client: TestClient, sample_pdf_bytes: bytes):
    """Everything the MVP promises, against the database a deployment runs."""
    email = f"{uuid.uuid4().hex[:8]}@example.com"
    assert (
        pg_client.post(
            "/api/auth/register",
            json={"email": email, "password": "a-good-long-passphrase"},
        ).status_code
        == 201
    )

    course = pg_client.post("/api/courses", json={"title": "Deep Learning"}).json()
    upload = pg_client.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": ("lecture.pdf", sample_pdf_bytes, "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    body = upload.json()
    assert body["status"] == "indexed"
    assert body["page_count"] == 9
    assert body["chunk_count"] > 0

    answer = pg_client.post(
        "/api/tutor/ask",
        json={"question": "Why is padding necessary?", "course_id": course["id"]},
    )
    assert answer.status_code == 200, answer.text
    result = answer.json()
    assert result["abstained"] is False
    assert result["evidence_sufficient"] is True
    # Both retrievers must have contributed on the real backends.
    assert 4 in [citation["page_number"] for citation in result["citations"]]
    retrievers = {name for source in result["sources"] for name in source["retrievers"]}
    assert "sparse" in retrievers, "tsvector search returned nothing"
    assert "dense" in retrievers, "pgvector search returned nothing"

    quiz = pg_client.post(
        "/api/assessments/generate",
        json={"course_id": course["id"], "question_count": 3},
    )
    assert quiz.status_code == 201, quiz.text
    questions = quiz.json()["questions"]
    assert questions

    submission = pg_client.post(
        f"/api/assessments/{quiz.json()['id']}/submit",
        json={
            "responses": [
                {"question_id": question["id"], "selected_option": 0, "answer_text": "padding"}
                for question in questions
            ]
        },
    )
    assert submission.status_code == 200
    assert submission.json()["mastery_changes"]

    dashboard = pg_client.get("/api/dashboard").json()
    assert dashboard["document_count"] == 1
    assert dashboard["indexed_chunk_count"] == body["chunk_count"]


def test_accounts_are_isolated_on_postgres(
    pg_client: TestClient, pg_settings: Settings, pg_context, sample_pdf_bytes: bytes
):
    owner = f"{uuid.uuid4().hex[:8]}@example.com"
    pg_client.post(
        "/api/auth/register", json={"email": owner, "password": "a-good-long-passphrase"}
    )
    course = pg_client.post("/api/courses", json={"title": "Private"}).json()
    pg_client.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": ("lecture.pdf", sample_pdf_bytes, "application/pdf")},
    )
    pg_client.post("/api/auth/logout")

    app = create_app(pg_settings, context=pg_context)
    with TestClient(app, base_url="https://testserver") as other:
        other.post(
            "/api/auth/register",
            json={
                "email": f"{uuid.uuid4().hex[:8]}@example.com",
                "password": "a-good-long-passphrase",
            },
        )
        assert other.get("/api/courses").json() == []
        assert other.get("/api/documents").json() == []
        # And retrieval must find nothing, not merely hide it from a listing.
        assert other.get("/api/dashboard").json()["indexed_chunk_count"] == 0
