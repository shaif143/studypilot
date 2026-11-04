"""Content-addressed blob storage for uploaded documents.

Files are stored under the user data directory keyed by SHA-256, which gives
free de-duplication and makes it impossible for a database row to point at a
file whose contents have silently changed.
"""

from __future__ import annotations

import hashlib
import shutil
import stat
from pathlib import Path


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def hash_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

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
        return self.path_for(content_hash).read_bytes()

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

    def clear(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
