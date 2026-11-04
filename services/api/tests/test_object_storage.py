"""The S3 blob store, against a simulated S3.

A hosted deployment keeps every uploaded PDF and every rendered page preview
in object storage, so a bug here loses a student's documents. The desktop
suite only ever exercises ``LocalBlobStore``, which is a different
implementation; these tests cover the one a deployment actually runs.

``moto`` simulates S3 in-process, so this needs no Docker, no credentials and
no network — and it runs in CI alongside everything else.
"""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from app.config import Settings
from app.storage.files import (
    BlobNotFoundError,
    LocalBlobStore,
    S3BlobStore,
    build_blob_store,
)

BUCKET = "studypilot-test"


@pytest.fixture
def s3_store(monkeypatch):
    # moto refuses to start without credentials in the environment, and these
    # must never be real ones.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        yield S3BlobStore(BUCKET, region="us-east-1")


# --- documents ------------------------------------------------------------
def test_put_is_content_addressed_and_idempotent(s3_store: S3BlobStore):
    data = b"%PDF-1.7 a lecture"
    first = s3_store.put(data)
    second = s3_store.put(data)

    assert first == second == S3BlobStore.hash_bytes(data)
    assert s3_store.exists(first)
    assert s3_store.read(first) == data


def test_different_bytes_get_different_keys(s3_store: S3BlobStore):
    a = s3_store.put(b"%PDF-1.7 lecture one")
    b = s3_store.put(b"%PDF-1.7 lecture two")
    assert a != b
    assert s3_store.read(a) != s3_store.read(b)


def test_reading_a_missing_document_raises_not_found(s3_store: S3BlobStore):
    with pytest.raises(BlobNotFoundError):
        s3_store.read("0" * 64)
    assert s3_store.exists("0" * 64) is False


def test_delete_removes_only_that_document(s3_store: S3BlobStore):
    keep = s3_store.put(b"%PDF-1.7 keep this")
    drop = s3_store.put(b"%PDF-1.7 drop this")

    s3_store.delete(drop)

    assert s3_store.exists(drop) is False
    assert s3_store.exists(keep) is True


def test_local_path_yields_a_real_file_and_cleans_up(s3_store: S3BlobStore):
    """PyMuPDF wants a path; a remote store has to materialise one."""
    data = b"%PDF-1.7 something to parse"
    content_hash = s3_store.put(data)

    with s3_store.local_path(content_hash) as path:
        assert path.is_file()
        assert path.read_bytes() == data
        captured = path

    # The temporary file must not survive the context.
    assert not captured.exists()


# --- derived assets (page previews) ---------------------------------------
def test_assets_round_trip_under_their_own_prefix(s3_store: S3BlobStore):
    s3_store.put_asset("doc-1/page-0001.webp", b"image-bytes", "image/webp")
    assert s3_store.read_asset("doc-1/page-0001.webp") == b"image-bytes"

    with pytest.raises(BlobNotFoundError):
        s3_store.read_asset("doc-1/page-9999.webp")


def test_deleting_a_documents_assets_leaves_other_documents_alone(s3_store: S3BlobStore):
    for page in range(3):
        s3_store.put_asset(f"doc-1/page-000{page}.webp", b"a", "image/webp")
    s3_store.put_asset("doc-2/page-0000.webp", b"b", "image/webp")

    s3_store.delete_asset_prefix("doc-1")

    with pytest.raises(BlobNotFoundError):
        s3_store.read_asset("doc-1/page-0000.webp")
    assert s3_store.read_asset("doc-2/page-0000.webp") == b"b"


def test_documents_and_assets_do_not_collide(s3_store: S3BlobStore):
    """They share a bucket, so the prefixes must keep them apart."""
    content_hash = s3_store.put(b"%PDF-1.7 a document")
    s3_store.put_asset(content_hash, b"an asset that happens to share the name", "image/png")

    assert s3_store.read(content_hash) == b"%PDF-1.7 a document"
    assert s3_store.read_asset(content_hash) == b"an asset that happens to share the name"


def test_clear_removes_everything(s3_store: S3BlobStore):
    """The delete-all-my-data control must leave nothing behind."""
    content_hash = s3_store.put(b"%PDF-1.7 a document")
    s3_store.put_asset("doc-1/page-0001.webp", b"image", "image/webp")

    s3_store.clear()

    assert s3_store.exists(content_hash) is False
    with pytest.raises(BlobNotFoundError):
        s3_store.read_asset("doc-1/page-0001.webp")


def test_clear_pages_through_more_than_one_delete_batch(s3_store: S3BlobStore):
    """The S3 delete API caps each request at 1000 keys."""
    for index in range(1050):
        s3_store.put_asset(f"bulk/page-{index:05d}.webp", b"x", "image/webp")

    s3_store.clear()

    with pytest.raises(BlobNotFoundError):
        s3_store.read_asset("bulk/page-00000.webp")
    with pytest.raises(BlobNotFoundError):
        s3_store.read_asset("bulk/page-01049.webp")


def test_a_prefix_separates_two_deployments_in_one_bucket(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        staging = S3BlobStore(BUCKET, region="us-east-1", prefix="staging")
        production = S3BlobStore(BUCKET, region="us-east-1", prefix="production")

        content_hash = staging.put(b"%PDF-1.7 staging only")

        assert staging.exists(content_hash) is True
        assert production.exists(content_hash) is False

        # Clearing one must not touch the other.
        production.clear()
        assert staging.exists(content_hash) is True


# --- selection ------------------------------------------------------------
def test_build_blob_store_follows_configuration(tmp_path, monkeypatch):
    local = build_blob_store(Settings(data_dir=tmp_path, storage_backend="local"))
    assert isinstance(local, LocalBlobStore)
    assert local.backend == "local"

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        remote = build_blob_store(
            Settings(
                data_dir=tmp_path,
                storage_backend="s3",
                s3_bucket=BUCKET,
                s3_region="us-east-1",
            )
        )
        assert isinstance(remote, S3BlobStore)
        assert remote.backend == "s3"


def test_s3_without_a_bucket_fails_loudly(tmp_path):
    """Better to refuse at startup than to lose uploads to a missing bucket."""
    with pytest.raises(RuntimeError, match="S3_BUCKET"):
        build_blob_store(Settings(data_dir=tmp_path, storage_backend="s3"))


def test_both_stores_agree_on_the_interface(tmp_path, s3_store: S3BlobStore):
    """The two implementations must be substitutable, which is the point."""
    local = LocalBlobStore(tmp_path / "blobs", tmp_path / "previews")
    data = b"%PDF-1.7 identical input"

    assert local.put(data) == s3_store.put(data)
    assert local.read(local.put(data)) == s3_store.read(s3_store.put(data))

    for store in (local, s3_store):
        key = store.put_asset("doc/page-0001.webp", b"image", "image/webp")
        assert key == "doc/page-0001.webp"
        assert store.read_asset(key) == b"image"
        store.delete_asset_prefix("doc")
        with pytest.raises(BlobNotFoundError):
            store.read_asset(key)
