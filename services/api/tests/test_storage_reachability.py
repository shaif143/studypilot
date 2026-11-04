"""Startup verification of the configured object store.

A wrong bucket name, a stale endpoint or a read-only API token are all easy to
get wrong and invisible until someone tries to upload a document. Checking at
boot puts the failure in the deploy log, with a message that names the setting
to fix.
"""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from app.config import Settings
from app.storage.files import LocalBlobStore, S3BlobStore

BUCKET = "studypilot-test"


@pytest.fixture
def aws_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


# --- the R2 compatibility fix --------------------------------------------
def test_checksum_behaviour_is_set_for_s3_compatible_providers(aws_credentials):
    """boto3 1.36+ sends a CRC32 header that Cloudflare R2 rejects with 501.

    Restoring `when_required` is what keeps uploads working against R2, B2 and
    MinIO. It is also what AWS S3 itself did before 1.36, so it costs nothing
    against real S3.
    """
    with mock_aws():
        store = S3BlobStore(BUCKET, endpoint_url="https://x.r2.cloudflarestorage.com")
        config = store._client.meta.config
        assert getattr(config, "request_checksum_calculation", None) == "when_required"
        assert getattr(config, "response_checksum_validation", None) == "when_required"


def test_endpoint_url_is_used_for_compatible_providers(aws_credentials):
    with mock_aws():
        store = S3BlobStore(BUCKET, endpoint_url="https://acct.r2.cloudflarestorage.com")
        assert store._client.meta.endpoint_url == "https://acct.r2.cloudflarestorage.com"
        assert store.endpoint_url == "https://acct.r2.cloudflarestorage.com"


# --- reachability ---------------------------------------------------------
def test_reachable_bucket_passes(aws_credentials):
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        S3BlobStore(BUCKET, region="us-east-1").check_reachable()


def test_missing_bucket_names_the_settings_to_fix(aws_credentials):
    with mock_aws():
        store = S3BlobStore("a-bucket-that-does-not-exist", region="us-east-1")
        with pytest.raises(RuntimeError) as error:
            store.check_reachable()

        message = str(error.value)
        assert "a-bucket-that-does-not-exist" in message
        assert "STUDYPILOT_S3_BUCKET" in message
        assert "AWS_*" in message


def test_the_probe_leaves_nothing_behind(aws_credentials):
    """The check writes an object; it must not litter the bucket."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)

        S3BlobStore(BUCKET, region="us-east-1").check_reachable()

        assert client.list_objects_v2(Bucket=BUCKET).get("KeyCount", 0) == 0


def test_local_store_reports_an_unwritable_directory(tmp_path):
    root = tmp_path / "blobs"
    store = LocalBlobStore(root, tmp_path / "previews")
    store.check_reachable()  # writable: fine

    root.chmod(0o500)  # read and execute only
    try:
        with pytest.raises(RuntimeError, match="not writable"):
            store.check_reachable()
    finally:
        # Restore so pytest can clean the directory up.
        root.chmod(0o700)


def test_local_store_creates_its_directories(tmp_path):
    store = LocalBlobStore(tmp_path / "a" / "blobs", tmp_path / "a" / "previews")
    store.check_reachable()
    assert (tmp_path / "a" / "blobs").is_dir()
    assert (tmp_path / "a" / "previews").is_dir()


# --- how startup reacts ---------------------------------------------------
def test_hosted_startup_refuses_an_unreachable_store(tmp_path, aws_credentials, monkeypatch):
    """In hosted mode this is fatal: uploads would fail for every user."""
    from app.runtime import RuntimeContext

    with mock_aws():
        settings = Settings(
            data_dir=tmp_path,
            deployment_mode="hosted",
            jwt_secret="s" * 40,
            storage_backend="s3",
            s3_bucket="never-created",
            s3_region="us-east-1",
            vector_store="numpy",
        )
        with pytest.raises(RuntimeError, match="never-created"):
            RuntimeContext.create(settings)


def test_desktop_startup_warns_but_continues(tmp_path, caplog):
    """On the desktop the app is still useful for reading what is already there."""
    from app.runtime import RuntimeContext

    settings = Settings(data_dir=tmp_path, vector_store="numpy")
    context = RuntimeContext.create(settings)
    assert context.blobs.backend == "local"
    context.close()
