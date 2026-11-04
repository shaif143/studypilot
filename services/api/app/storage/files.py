"""Binary storage for uploaded documents and rendered page previews.

Two implementations behind one interface:

``LocalBlobStore``
    Content-addressed files under the user data directory. The desktop default.
``S3BlobStore``
    Any S3-compatible object store (AWS S3, Cloudflare R2, Backblaze B2,
    MinIO). The hosted default, because a serverless or container host has no
    durable local disk.

Documents are keyed by the SHA-256 of their bytes, which gives free
de-duplication and makes it impossible for a database row to point at a file
whose contents have silently changed. Derived assets — page previews — are
keyed by an explicit path instead, because they are regenerable and belong to
a particular document rather than to a particular byte sequence.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import stat
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class BlobNotFoundError(KeyError):
    """The requested object is not in the store."""


class BlobStore(ABC):
    @property
    @abstractmethod
    def backend(self) -> str: ...

    @staticmethod
    def hash_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    # --- documents -------------------------------------------------------
    @abstractmethod
    def put(self, data: bytes) -> str:
        """Store bytes, returning their content hash."""

    @abstractmethod
    def read(self, content_hash: str) -> bytes: ...

    @abstractmethod
    def exists(self, content_hash: str) -> bool: ...

    @abstractmethod
    def delete(self, content_hash: str) -> None: ...

    @contextmanager
    def local_path(self, content_hash: str) -> Iterator[Path]:
        """Yield a filesystem path to the object.

        PyMuPDF is happiest with a real file. The local store yields its own
        path directly; a remote store downloads to a temporary file and removes
        it afterwards, so callers never need to know which is in use.
        """
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as handle:
            handle.write(self.read(content_hash))
            handle.flush()
            yield Path(handle.name)

    # --- derived assets (page previews) ----------------------------------
    @abstractmethod
    def put_asset(self, key: str, data: bytes, content_type: str) -> str:
        """Store a derived asset under an explicit key; returns that key."""

    @abstractmethod
    def read_asset(self, key: str) -> bytes: ...

    @abstractmethod
    def delete_asset_prefix(self, prefix: str) -> None:
        """Remove every asset whose key starts with ``prefix``."""

    # --- lifecycle -------------------------------------------------------
    @abstractmethod
    def clear(self) -> None:
        """Remove everything. Used by the delete-all-my-data control."""

    def check_reachable(self) -> None:
        """Raise if the store is not usable with the current configuration.

        Called at startup so a wrong bucket name, a bad endpoint or expired
        credentials surface in the deploy log — not silently, on the first
        upload a student attempts.
        """
        return None


class LocalBlobStore(BlobStore):
    def __init__(self, root: Path, asset_root: Path | None = None) -> None:
        self.root = root
        self.asset_root = asset_root or root.parent / "previews"
        self.root.mkdir(parents=True, exist_ok=True)
        self.asset_root.mkdir(parents=True, exist_ok=True)

    @property
    def backend(self) -> str:
        return "local"

    def path_for(self, content_hash: str) -> Path:
        # Two-level fan-out keeps directory sizes reasonable.
        return self.root / content_hash[:2] / content_hash[2:4] / content_hash

    def exists(self, content_hash: str) -> bool:
        return self.path_for(content_hash).exists()

    def put(self, data: bytes) -> str:
        content_hash = self.hash_bytes(data)
        target = self.path_for(content_hash)
        if target.exists():
            return content_hash
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".partial")
        tmp.write_bytes(data)
        tmp.chmod(stat.S_IRUSR | stat.S_IWUSR)
        tmp.replace(target)
        return content_hash

    def read(self, content_hash: str) -> bytes:
        target = self.path_for(content_hash)
        if not target.is_file():
            raise BlobNotFoundError(content_hash)
        return target.read_bytes()

    @contextmanager
    def local_path(self, content_hash: str) -> Iterator[Path]:
        target = self.path_for(content_hash)
        if not target.is_file():
            raise BlobNotFoundError(content_hash)
        yield target

    def delete(self, content_hash: str) -> None:
        target = self.path_for(content_hash)
        if target.exists():
            target.unlink()
        # Clean up now-empty fan-out directories.
        for parent in (target.parent, target.parent.parent):
            try:
                parent.rmdir()
            except OSError:
                break

    def _asset_path(self, key: str) -> Path:
        # The key is built from ids we generate, never from user input, but it
        # is still confined to the asset root so a bad caller cannot escape it.
        resolved = (self.asset_root / key).resolve()
        if not resolved.is_relative_to(self.asset_root.resolve()):
            raise BlobNotFoundError(key)
        return resolved

    def put_asset(self, key: str, data: bytes, content_type: str) -> str:
        target = self._asset_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return key

    def read_asset(self, key: str) -> bytes:
        target = self._asset_path(key)
        if not target.is_file():
            raise BlobNotFoundError(key)
        return target.read_bytes()

    def delete_asset_prefix(self, prefix: str) -> None:
        target = self._asset_path(prefix)
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.is_file():
            target.unlink()

    def clear(self) -> None:
        for root in (self.root, self.asset_root):
            if root.exists():
                shutil.rmtree(root)
            root.mkdir(parents=True, exist_ok=True)

    def check_reachable(self) -> None:
        for root in (self.root, self.asset_root):
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".studypilot-write-probe"
            try:
                probe.write_bytes(b"ok")
                probe.unlink()
            except OSError as exc:
                raise RuntimeError(f"{root} is not writable: {exc}") from exc


class S3BlobStore(BlobStore):
    """Any S3-compatible object store.

    ``endpoint_url`` is what makes this work with R2, B2 or MinIO as well as
    AWS. Credentials come from the environment or the instance role; they are
    never read from the frontend or stored in the database.
    """

    DOCUMENT_PREFIX = "documents/"
    ASSET_PREFIX = "previews/"

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        region: str | None = None,
        prefix: str = "",
    ) -> None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - optional extra
            raise RuntimeError("S3 storage needs boto3. Install the 'hosted' extra.") from exc

        self.bucket = bucket
        # Neon Object Storage injects the AWS-standard variables rather than
        # ours, so fall back to them. That makes `neon env pull` output work
        # as-is, with no provider-specific settings to copy across.
        self.endpoint_url = endpoint_url or os.environ.get("AWS_ENDPOINT_URL_S3", "")
        region = region or os.environ.get("AWS_REGION", "")
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""

        # boto3 1.36 began sending `x-amz-checksum-crc32` on every upload by
        # default. Cloudflare R2 answers that header with 501 NotImplemented,
        # and several other S3-compatible providers reject it too, so an
        # upload fails with a message that says nothing about checksums.
        # `when_required` restores the pre-1.36 behaviour; it is also what AWS
        # S3 itself did until then, so this costs nothing against real S3.
        options: dict[str, Any] = {
            "retries": {"max_attempts": 3, "mode": "standard"},
            # Neon Object Storage accepts path-style addressing only, and R2
            # and MinIO both prefer it. boto3's "auto" default picks
            # virtual-hosted style against a custom endpoint, which those
            # providers reject — so it is pinned rather than negotiated.
            "s3": {"addressing_style": "path"},
        }
        for option in ("request_checksum_calculation", "response_checksum_validation"):
            # Guard on support so an older botocore does not fail to construct.
            if option in Config.OPTION_DEFAULTS:
                options[option] = "when_required"
        self._client = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url or None,
            region_name=region or None,
            config=Config(**options),
        )

    @property
    def backend(self) -> str:
        return "s3"

    def _document_key(self, content_hash: str) -> str:
        return f"{self.prefix}{self.DOCUMENT_PREFIX}{content_hash}"

    def _asset_key(self, key: str) -> str:
        return f"{self.prefix}{self.ASSET_PREFIX}{key.lstrip('/')}"

    def put(self, data: bytes) -> str:
        content_hash = self.hash_bytes(data)
        if not self.exists(content_hash):
            self._client.put_object(
                Bucket=self.bucket,
                Key=self._document_key(content_hash),
                Body=data,
                ContentType="application/pdf",
            )
        return content_hash

    def read(self, content_hash: str) -> bytes:
        try:
            response = self._client.get_object(
                Bucket=self.bucket, Key=self._document_key(content_hash)
            )
        except self._client.exceptions.NoSuchKey as exc:
            raise BlobNotFoundError(content_hash) from exc
        return response["Body"].read()

    def exists(self, content_hash: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=self._document_key(content_hash))
        except Exception:
            return False
        return True

    def delete(self, content_hash: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=self._document_key(content_hash))

    def put_asset(self, key: str, data: bytes, content_type: str) -> str:
        self._client.put_object(
            Bucket=self.bucket,
            Key=self._asset_key(key),
            Body=data,
            ContentType=content_type,
        )
        return key

    def read_asset(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=self._asset_key(key))
        except self._client.exceptions.NoSuchKey as exc:
            raise BlobNotFoundError(key) from exc
        return response["Body"].read()

    def _iter_keys(self, prefix: str) -> Iterator[str]:
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                yield item["Key"]

    def _delete_keys(self, prefix: str) -> None:
        batch: list[dict[str, str]] = []
        for key in self._iter_keys(prefix):
            batch.append({"Key": key})
            # The delete_objects API caps each request at 1000 keys.
            if len(batch) == 1000:
                self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": batch})
                batch = []
        if batch:
            self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": batch})

    def delete_asset_prefix(self, prefix: str) -> None:
        self._delete_keys(self._asset_key(prefix))

    def clear(self) -> None:
        self._delete_keys(f"{self.prefix}{self.DOCUMENT_PREFIX}")
        self._delete_keys(f"{self.prefix}{self.ASSET_PREFIX}")

    def check_reachable(self) -> None:
        """Confirm the bucket exists and the credentials can read and write it.

        A read-only probe would pass with credentials that cannot upload, so
        this writes and deletes a small object — the same operations an
        upload performs.
        """
        probe_key = f"{self.prefix}.studypilot-write-probe"
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except Exception as exc:
            raise RuntimeError(
                f"Cannot reach bucket '{self.bucket}'"
                + (f" at {self.endpoint_url}" if self.endpoint_url else "")
                + f": {exc}. Check STUDYPILOT_S3_BUCKET, "
                "STUDYPILOT_S3_ENDPOINT_URL and the AWS_* credentials."
            ) from exc
        try:
            self._client.put_object(Bucket=self.bucket, Key=probe_key, Body=b"ok")
            self._client.delete_object(Bucket=self.bucket, Key=probe_key)
        except Exception as exc:
            raise RuntimeError(
                f"Bucket '{self.bucket}' is reachable but not writable: {exc}. "
                "The API token needs object read *and* write permission."
            ) from exc


def build_blob_store(settings) -> BlobStore:
    """Build the store the configuration asks for."""
    if settings.storage_backend == "s3":
        if not settings.s3_bucket:
            raise RuntimeError("STUDYPILOT_STORAGE_BACKEND=s3 requires STUDYPILOT_S3_BUCKET.")
        return S3BlobStore(
            settings.s3_bucket,
            endpoint_url=settings.s3_endpoint_url,
            region=settings.s3_region,
            prefix=settings.s3_prefix,
        )
    return LocalBlobStore(settings.blob_dir, settings.preview_dir)
