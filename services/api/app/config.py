"""Application configuration.

All runtime configuration is resolved here so that the rest of the code never
reads environment variables directly.  Defaults are chosen to be safe and
local-first: no network egress, no cloud model providers, data stored under the
platform user-data directory.
"""

from __future__ import annotations

import os
import secrets
import stat
import sys
from contextlib import suppress
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "StudyPilot"


def default_data_dir() -> Path:
    """Return the per-user data directory appropriate for the host platform."""
    override = os.environ.get("STUDYPILOT_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":  # pragma: no cover - not a supported target
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    return Path(xdg).expanduser() / APP_NAME if xdg else Path.home() / ".local" / "share" / APP_NAME


class Settings(BaseSettings):
    """Typed runtime settings.

    Environment variables are prefixed with ``STUDYPILOT_``.
    """

    model_config = SettingsConfigDict(
        env_prefix="STUDYPILOT_",
        env_file=".env",
        extra="ignore",
    )

    # --- Paths -----------------------------------------------------------
    data_dir: Path = Field(default_factory=default_data_dir)

    # --- HTTP ------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8765
    #: Origins allowed to call the API. Desktop uses the tauri:// scheme.
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "tauri://localhost",
        "http://tauri.localhost",
    )

    # --- Authentication --------------------------------------------------
    #: When provided (desktop sidecar), this token is used verbatim. Otherwise a
    #: token is generated once and persisted with 0600 permissions.
    api_token: str | None = None
    #: Disable auth only for unit tests; never for a running application.
    auth_disabled: bool = False

    # --- Upload limits ---------------------------------------------------
    max_upload_bytes: int = 80 * 1024 * 1024
    max_pdf_pages: int = 2000

    # --- Model providers -------------------------------------------------
    #: "extractive" is the offline, non-generative default. See docs/DECISIONS.md.
    llm_provider: str = "extractive"
    llm_model: str = ""
    #: "hash" needs no download and no network; "model2vec"/"sentence-transformers"
    #: are higher quality and installed via optional extras.
    embedding_provider: str = "hash"
    embedding_model: str = ""
    #: Explicit, per-install consent gate for sending content to a cloud provider.
    allow_cloud_processing: bool = False

    # --- Retrieval -------------------------------------------------------
    vector_store: str = "qdrant-local"
    retrieval_top_k: int = 8
    retrieval_candidates: int = 40
    #: Minimum fused score for evidence to count toward sufficiency.
    sufficiency_min_score: float = 0.02
    sufficiency_min_chunks: int = 2
    #: Fraction of the question's content words that must appear in the
    #: retrieved passages before an answer is treated as grounded.
    sufficiency_min_coverage: float = 0.34

    # --- Chunking --------------------------------------------------------
    child_chunk_chars: int = 900
    child_chunk_overlap: int = 150

    # --- Logging ---------------------------------------------------------
    log_level: str = "INFO"
    #: When False (the default) prompts, document text and answers are never logged.
    log_sensitive_content: bool = False

    # --- Derived paths ---------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.data_dir / "studypilot.sqlite3"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "documents"

    @property
    def preview_dir(self) -> Path:
        return self.data_dir / "previews"

    @property
    def vector_dir(self) -> Path:
        return self.data_dir / "vectors"

    @property
    def token_path(self) -> Path:
        return self.data_dir / "api_token"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.blob_dir, self.preview_dir, self.vector_dir):
            path.mkdir(parents=True, exist_ok=True)
        # The data directory holds private academic material: owner-only.
        # pragma: no cover - chmod can fail on unusual filesystems
        with suppress(OSError):
            self.data_dir.chmod(stat.S_IRWXU)

    def resolve_api_token(self) -> str:
        """Return the shared secret for the local API, creating it if needed."""
        if self.api_token:
            return self.api_token
        self.ensure_dirs()
        if self.token_path.exists():
            token = self.token_path.read_text(encoding="utf-8").strip()
            if token:
                return token
        token = secrets.token_urlsafe(32)
        self.token_path.write_text(token, encoding="utf-8")
        self.token_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return token


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
