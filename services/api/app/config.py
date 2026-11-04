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
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
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

    # --- Deployment ------------------------------------------------------
    #: "desktop" keeps the single local profile and local storage. "hosted"
    #: requires real authentication and refuses to start without a JWT secret.
    deployment_mode: Literal["desktop", "hosted"] = "desktop"

    # --- Paths -----------------------------------------------------------
    data_dir: Path = Field(default_factory=default_data_dir)

    # --- Database --------------------------------------------------------
    #: A PostgreSQL DSN for hosted deployments. Empty means the local SQLite
    #: file under ``data_dir``. Falls back to the provider-standard
    #: ``DATABASE_URL`` so `neon env pull` output works unmodified.
    database_url: str = Field(
        default="", validation_alias=AliasChoices("STUDYPILOT_DATABASE_URL", "DATABASE_URL")
    )
    #: A *direct* (non-pooled) DSN for schema work. Neon routes the pooled
    #: connection through PgBouncer in transaction mode, which cannot hold the
    #: session state that DDL, CREATE EXTENSION and index builds rely on.
    #: Optional: without it everything uses the one connection.
    database_url_direct: str = Field(
        default="",
        validation_alias=AliasChoices("STUDYPILOT_DATABASE_URL_DIRECT", "DATABASE_URL_UNPOOLED"),
    )

    # --- Object storage --------------------------------------------------
    storage_backend: Literal["local", "s3"] = "local"
    s3_bucket: str = ""
    #: Set for S3-compatible providers (Cloudflare R2, Backblaze B2, MinIO).
    s3_endpoint_url: str = ""
    s3_region: str = ""
    s3_prefix: str = ""

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
    #: Desktop: a shared secret between the Tauri shell and the sidecar. When
    #: provided it is used verbatim; otherwise one is generated and persisted
    #: with 0600 permissions. Not used in hosted mode.
    api_token: str | None = None
    #: Disable auth only for unit tests; never for a running application.
    auth_disabled: bool = False
    # --- Neon Auth (optional alternative identity provider) --------------
    #: "builtin" uses this application's own email/password accounts.
    #: "neon" delegates identity to Neon Auth and verifies its JWTs.
    auth_provider: Literal["builtin", "neon"] = "builtin"
    #: Injected by `neon deploy` / `neon env pull` under these exact names.
    neon_auth_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("STUDYPILOT_NEON_AUTH_BASE_URL", "NEON_AUTH_BASE_URL"),
    )
    neon_auth_jwks_url: str = Field(
        default="",
        validation_alias=AliasChoices("STUDYPILOT_NEON_AUTH_JWKS_URL", "NEON_AUTH_JWKS_URL"),
    )

    @property
    def uses_neon_auth(self) -> bool:
        return self.auth_provider == "neon"

    #: Hosted mode: signs session tokens. Must be set explicitly — a generated
    #: default would invalidate every session on each restart and differ
    #: between instances.
    jwt_secret: str = ""
    access_token_hours: int = 24 * 14
    #: Whether new accounts may be created. Turn off once yours exists.
    allow_registration: bool = True
    #: Argon2id parameters. The defaults target roughly 50 ms on a small
    #: container, which is the usual balance between cost to an attacker and
    #: latency for a legitimate sign-in.
    argon2_time_cost: int = 2
    argon2_memory_kib: int = 64 * 1024
    argon2_parallelism: int = 2

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
    #: "qdrant-local" (embedded, desktop default) | "pgvector" (inside the
    #: PostgreSQL database, recommended for hosted) | "qdrant-remote" | "numpy"
    vector_store: str = "qdrant-local"
    #: Required when vector_store is "qdrant-remote".
    qdrant_url: str = ""
    qdrant_api_key: str = ""
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

    @property
    def is_hosted(self) -> bool:
        return self.deployment_mode == "hosted"

    @model_validator(mode="after")
    def _check_hosted_requirements(self) -> Settings:
        """Refuse to start a hosted deployment without an identity boundary.

        Only the identity requirements are fatal, because getting them wrong is
        not recoverable: without a signing secret there are no sessions, and
        with auth disabled every visitor shares one profile and can read
        everything anyone uploads.

        Storage durability is checked separately, at startup, because the right
        answer depends on the host — SQLite on a container with a persistent
        volume is perfectly sound, while the same configuration on a serverless
        platform loses every upload. That distinction cannot be made from the
        configuration alone, so it is reported as a warning rather than guessed
        at here.
        """
        if not self.is_hosted:
            return self
        problems: list[str] = []
        if self.uses_neon_auth and not self.neon_auth_jwks_url:
            problems.append(
                "STUDYPILOT_AUTH_PROVIDER=neon requires NEON_AUTH_JWKS_URL (run `neon deploy`)"
            )
        # With Neon Auth, Neon signs the tokens and this service only verifies
        # them, so there is no local secret to require.
        if not self.uses_neon_auth and len(self.jwt_secret) < 32:
            problems.append(
                "STUDYPILOT_JWT_SECRET must be set to at least 32 characters "
                "(generate one with: openssl rand -base64 48)"
            )
        if self.auth_disabled:
            problems.append("STUDYPILOT_AUTH_DISABLED must be false in hosted mode")
        if problems:
            raise ValueError("Hosted mode is misconfigured: " + "; ".join(problems))
        return self

    def durability_warnings(self) -> list[str]:
        """Configuration that will lose data on an ephemeral filesystem.

        Returned rather than raised: these are correct on a host with a
        persistent volume and fatal on a serverless one.
        """
        if not self.is_hosted:
            return []
        warnings: list[str] = []
        if not self.database_url:
            warnings.append(
                "Using a local SQLite file. Correct on a host with a persistent "
                "volume; on a serverless platform every upload will be lost. "
                "Set STUDYPILOT_DATABASE_URL to use PostgreSQL."
            )
        if self.storage_backend == "local":
            warnings.append(
                "Uploaded files are being written to local disk. Set "
                "STUDYPILOT_STORAGE_BACKEND=s3 with STUDYPILOT_S3_BUCKET for "
                "durable object storage."
            )
        if self.vector_store == "qdrant-local":
            warnings.append(
                "The vector index is embedded on local disk and cannot be shared "
                "between instances. Set STUDYPILOT_VECTOR_STORE=pgvector to keep "
                "it in PostgreSQL, or qdrant-remote with STUDYPILOT_QDRANT_URL."
            )
        return warnings

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
