"""Shared test fixtures.

Every test gets an isolated data directory and an in-process numpy vector store,
so the suite needs no network, no model download and no Qdrant directory lock.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, reset_settings_cache
from app.main import create_app
from app.models.embeddings import HashingEmbedder
from app.rag.sparse import SparseIndex
from app.rag.vector_store import NumpyVectorStore
from app.runtime import RuntimeContext
from app.storage.db import Database
from app.storage.files import BlobStore

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
SAMPLE_PDF = FIXTURES / "sample-lecture-cnn.pdf"


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        auth_disabled=False,
        api_token="test-token",
        vector_store="numpy",
        embedding_provider="hash",
        llm_provider="extractive",
        allow_cloud_processing=False,
        log_level="WARNING",
    )


@pytest.fixture
def context(settings: Settings) -> RuntimeContext:
    settings.ensure_dirs()
    database = Database.from_settings(settings)
    database.create_all()
    embedder = HashingEmbedder()
    store = NumpyVectorStore()
    store.ensure_collection(embedder.dimensions)
    ctx = RuntimeContext(
        settings=settings,
        database=database,
        blobs=BlobStore(settings.blob_dir),
        vector_store=store,
        sparse_index=SparseIndex(database),
        embedder=embedder,
    )
    yield ctx
    ctx.close()


@pytest.fixture
def client(settings: Settings, context: RuntimeContext) -> TestClient:
    app = create_app(settings, context=context)
    with TestClient(app) as test_client:
        test_client.headers.update({"x-studypilot-token": "test-token"})
        yield test_client


@pytest.fixture
def sample_pdf_bytes() -> bytes:
    if not SAMPLE_PDF.is_file():
        pytest.skip(f"Fixture missing: {SAMPLE_PDF}. Run: python scripts/make_fixtures.py")
    return SAMPLE_PDF.read_bytes()


@pytest.fixture
def course(client: TestClient) -> dict:
    response = client.post(
        "/api/courses",
        json={"title": "Deep Learning", "code": "CS3410", "institution": "Test University"},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def indexed_document(client: TestClient, course: dict, sample_pdf_bytes: bytes) -> dict:
    response = client.post(
        f"/api/courses/{course['id']}/documents",
        files={"file": ("sample-lecture-cnn.pdf", sample_pdf_bytes, "application/pdf")},
        data={"title": "Lecture 7 - CNNs"},
    )
    assert response.status_code == 201, response.text
    return response.json()
