"""Hybrid retrieval, fusion, access control and the sufficiency check."""

from __future__ import annotations

import pytest

from app.models.embeddings import HashingEmbedder
from app.rag.fusion import reciprocal_rank_fusion
from app.rag.retriever import HybridRetriever, classify_query
from app.rag.sparse import SparseIndex
from app.rag.vector_store import NumpyVectorStore, VectorRecord
from app.storage.repositories import (
    ChunkRepository,
    CourseRepository,
    DocumentRepository,
    UserRepository,
)


def test_rrf_rewards_agreement_between_retrievers():
    fused = reciprocal_rank_fusion(
        {
            "dense": [("a", 0.9), ("b", 0.8), ("c", 0.7)],
            "sparse": [("c", 5.0), ("a", 4.0)],
        }
    )
    # "a" is high in both lists; "b" is only in one.
    assert fused[0].chunk_id == "a"
    assert set(fused[0].ranks) == {"dense", "sparse"}
    assert fused[-1].chunk_id == "b"


def test_rrf_weights_shift_the_ordering():
    lists = {"dense": [("x", 1.0)], "sparse": [("y", 1.0)]}
    sparse_heavy = reciprocal_rank_fusion(lists, weights={"dense": 0.1, "sparse": 2.0})
    assert sparse_heavy[0].chunk_id == "y"


def test_query_classification():
    assert classify_query("What is padding?") == "definition"
    assert classify_query("Derive the output size formula") == "formula"
    assert classify_query("Why is padding necessary?") == "explanation"
    assert classify_query("padding stride pooling") == "general"


def test_hashing_embedder_is_deterministic_and_normalised():
    embedder = HashingEmbedder()
    first = embedder.embed_query("convolution and padding")
    second = embedder.embed_query("convolution and padding")
    assert first == second
    norm = sum(value * value for value in first) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-6)
    assert embedder.embed_query("") == [0.0] * embedder.dimensions


def test_vector_store_respects_the_allow_list():
    store = NumpyVectorStore()
    store.ensure_collection(3)
    store.upsert(
        [
            VectorRecord("mine", [1.0, 0.0, 0.0], {}),
            VectorRecord("theirs", [1.0, 0.0, 0.0], {}),
        ]
    )
    hits = store.search([1.0, 0.0, 0.0], limit=10, allowed_chunk_ids={"mine"})
    assert [hit.chunk_id for hit in hits] == ["mine"]
    assert store.search([1.0, 0.0, 0.0], limit=10, allowed_chunk_ids=set()) == []


def _build_retriever(context, session):
    return HybridRetriever(
        settings=context.settings,
        embedder=context.embedder,
        vector_store=context.vector_store,
        sparse_index=SparseIndex(context.database),
        chunks=ChunkRepository(session),
        documents=DocumentRepository(session),
    )


def test_retrieval_finds_the_right_page(client, indexed_document, context):
    """Retrieval must land on the page that actually defines the concept."""
    with context.database.session() as session:
        user = UserRepository(session).ensure_local_profile()
        retriever = _build_retriever(context, session)
        outcome = retriever.retrieve("Why is padding necessary?", user_id=user.id)

    assert outcome.chunks, "expected at least one retrieved chunk"
    pages = [chunk.page_number for chunk in outcome.chunks[:3]]
    # Padding is slide 4 in the fixture deck.
    assert 4 in pages
    assert any("padding" in chunk.text.lower() for chunk in outcome.chunks)


def test_retrieval_is_scoped_to_the_owner(client, indexed_document, context):
    """Another user's id must retrieve nothing, not another user's material."""
    with context.database.session() as session:
        retriever = _build_retriever(context, session)
        outcome = retriever.retrieve("padding", user_id="someone-else")
    assert outcome.chunks == []
    assert outcome.sufficient is False
    assert "no indexed material" in outcome.reason


def test_retrieval_scoped_to_a_document(
    client, course, indexed_document, context, sample_pdf_bytes
):
    with context.database.session() as session:
        user = UserRepository(session).ensure_local_profile()
        retriever = _build_retriever(context, session)
        outcome = retriever.retrieve(
            "padding", user_id=user.id, document_ids=[indexed_document["document"]["id"]]
        )
    assert outcome.chunks
    assert {chunk.document_id for chunk in outcome.chunks} == {indexed_document["document"]["id"]}


def test_unanswerable_question_is_marked_insufficient(client, indexed_document, context):
    """A question the material does not cover must not be reported as grounded."""
    with context.database.session() as session:
        user = UserRepository(session).ensure_local_profile()
        retriever = _build_retriever(context, session)
        outcome = retriever.retrieve(
            "What is the capital of Portugal and its population?", user_id=user.id
        )
    assert outcome.sufficient is False
    assert outcome.reason


def test_corrective_retrieval_reformulates(client, indexed_document, context):
    with context.database.session() as session:
        user = UserRepository(session).ensure_local_profile()
        retriever = _build_retriever(context, session)
        outcome = retriever.retrieve(
            "Could you possibly tell me what on earth a zzzz is?", user_id=user.id
        )
    # A reformulation was attempted and recorded.
    assert outcome.reformulated_query is not None


def test_sparse_index_handles_fts_syntax_in_questions(client, indexed_document, context):
    """FTS5 operators typed by a student must not break or inject into the query."""
    with context.database.session() as session:
        user = UserRepository(session).ensure_local_profile()
        index = SparseIndex(context.database)
        hits = index.search(session, 'padding OR "NEAR(x y)" AND *', user_id=user.id, limit=5)
    assert isinstance(hits, list)


def test_deleting_a_document_removes_it_from_retrieval(client, indexed_document, context):
    document_id = indexed_document["document"]["id"]
    assert client.delete(f"/api/documents/{document_id}").status_code == 204

    with context.database.session() as session:
        user = UserRepository(session).ensure_local_profile()
        retriever = _build_retriever(context, session)
        outcome = retriever.retrieve("padding", user_id=user.id)
    assert outcome.chunks == []


def test_course_scope_excludes_other_courses(
    client, course, indexed_document, context, sample_pdf_bytes
):
    other = client.post("/api/courses", json={"title": "Other course"}).json()
    with context.database.session() as session:
        user = UserRepository(session).ensure_local_profile()
        CourseRepository(session).get(user.id, other["id"])
        retriever = _build_retriever(context, session)
        outcome = retriever.retrieve("padding", user_id=user.id, course_ids=[other["id"]])
    assert outcome.chunks == []
