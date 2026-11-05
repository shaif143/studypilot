"""Application configuration.

All runtime configuration is resolved here so that the rest of the code never
reads environment variables directly.  Defaults are chosen to be safe and
local-first: no network egress, no cloud model providers, data stored under the
platform user-data directory.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
import sys
from contextlib import suppress
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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
        # Only `.env`. `.env.local` holds the hosted deployment's real
        # connection strings and API keys, and reading it here would make
        # every Settings() in the test suite — whose fixtures drop and
        # recreate the schema — point at the production database. The dev
        # script exports `.env.local` into the environment instead.
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
    #:
    #: These defaults are for local development only. A hosted deployment
    #: ignores them entirely and allows nothing unless the environment names
    #: the origins explicitly — see `effective_cors_origins`.
    #:
    #: `NoDecode` because the natural way to write this in a hosting
    #: platform's environment form is a comma-separated list, and without it
    #: pydantic-settings insists on JSON and fails before the validator below
    #: ever sees the value.
    cors_origins: Annotated[tuple[str, ...], NoDecode] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "tauri://localhost",
        "http://tauri.localhost",
    )

    # --- Assignment guide ------------------------------------------------
    #: Provider for drafting assignment guides, if different from the tutor's.
    #: Empty means: Anthropic when a key is present, otherwise the tutor's
    #: provider. Set explicitly to pin it.
    assignment_provider: str = ""
    #: Which model reads image briefs. Empty means the assignment provider.
    vision_provider: str = ""
    #: How audio briefs are transcribed: "auto" tries a local Whisper model
    #: first and falls back to a cloud provider that accepts audio (with
    #: consent); "local" and "cloud" pin one; "off" refuses audio uploads.
    audio_transcriber: Literal["auto", "local", "cloud", "off"] = "auto"
    #: faster-whisper model size for local transcription. "small" fits an
    #: 8 GB Mac; "medium" is better and needs about 2 GB more.
    whisper_model: str = "small"

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
    #: Where this API is reachable from a browser, for OAuth redirects on a
    #: hosted deployment ("https://api.example.com"). Derived from the request
    #: when empty, which is right behind Render's proxy and wrong behind one
    #: that does not forward the original host.
    public_api_url: str = ""
    #: The website's origin, where a hosted OAuth callback sends the browser
    #: back. Defaults to the first CORS origin, then to the API's own origin.
    web_origin: str = ""
    #: Hosted only: encrypts stored integration tokens (Google Drive). The
    #: JWT secret is used when this is empty. The desktop generates its own
    #: key into the Keychain.
    token_encryption_key: str = ""
    max_pdf_pages: int = 2000

    # --- Model providers -------------------------------------------------
    #: "extractive" is the offline, non-generative default. See docs/DECISIONS.md.
    llm_provider: str = "extractive"
    llm_model: str = ""
    #: "hash" needs no download and no network; "model2vec"/"sentence-transformers"
    #: are higher quality and installed via optional extras.
    embedding_provider: str = "hash"
    embedding_model: str = ""
    #: The operator's switch for sending content to a cloud provider. It is
    #: one of three layers — see `cloud_processing_permitted` — and only the
    #: hosted deployment has an operator distinct from the user, so only
    #: there does its default of False bite.
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

    @property
    def cloud_processing_permitted(self) -> bool:
        """Whether this installation may send content to a cloud provider at all.

        Cloud use needs three affirmations: this one from whoever runs the
        service, the user's own Settings toggle, and a flag on the individual
        request. On the desktop the person running the service *is* the user,
        so an unset operator switch defers to the other two — otherwise the
        Settings toggle would be a control that could never do anything,
        because nobody launches the packaged app with an environment variable.
        A hosted deployment keeps the operator's explicit say: its default is
        no. Setting STUDYPILOT_ALLOW_CLOUD_PROCESSING explicitly wins in
        either mode.
        """
        if "allow_cloud_processing" in self.model_fields_set:
            return self.allow_cloud_processing
        return self.allow_cloud_processing or not self.is_hosted

    @property
    def effective_cors_origins(self) -> tuple[str, ...]:
        """Origins this deployment actually grants cross-origin access to.

        A hosted deployment served behind a reverse proxy that rewrites
        `/api/*` is same-origin and needs none, which is why the answer here
        is nothing at all unless `STUDYPILOT_CORS_ORIGINS` is set explicitly.
        The development defaults never leak into a deployment: localhost on a
        student's machine must not be able to read a hosted account.

        Naming origins is the supported arrangement when the site and the API
        are on different hosts — a static site on one platform and this
        container on another. It is safe because the list is explicit, no
        wildcard is accepted, and credentials are never allowed, so a browser
        will not attach cookies to a cross-origin request. Identity therefore
        has to travel in the `Authorization` header, which is what Neon Auth
        does; the built-in cookie sessions only work same-origin.
        """
        if not self.is_hosted:
            return self.cors_origins
        if "cors_origins" not in self.model_fields_set:
            return ()
        return self.cors_origins

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_origins(cls, value: object) -> object:
        """Accept a comma-separated list, and refuse a wildcard.

        Comma-separated because that is what fits in a hosting platform's
        environment form; a JSON array still works for anyone who prefers it.

        `*` is rejected rather than filtered out: a configuration that says
        "allow everything" and silently allows nothing is worse than one that
        refuses to start, and allowing every site on the internet to call an
        authenticated API is never what was meant.
        """
        if isinstance(value, str):
            text = value.strip()
            value = json.loads(text) if text.startswith("[") else text.split(",")
        if not isinstance(value, (list, tuple)):
            return value
        origins = [str(origin).strip().rstrip("/") for origin in value]
        if any(origin == "*" for origin in origins):
            raise ValueError(
                "STUDYPILOT_CORS_ORIGINS must name each allowed origin; '*' is not accepted."
            )
        return tuple(origin for origin in origins if origin)

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

    def configuration_warnings(self) -> list[str]:
        """Everything worth saying loudly at boot but not worth refusing over.

        Durability, plus the one cross-origin arrangement that cannot work:
        browsers do not attach cookies to a cross-origin request unless the
        response allows credentials, and this API never does. A deployment
        that names foreign origins and still relies on the built-in cookie
        sessions would accept every request as anonymous and show a sign-in
        screen that can never be got past.
        """
        warnings = self.durability_warnings()
        if self.is_hosted and self.effective_cors_origins and not self.uses_neon_auth:
            warnings.append(
                "Cross-origin access is configured ("
                + ", ".join(self.effective_cors_origins)
                + ") but identity is the built-in cookie session, which a "
                "browser will not send cross-origin. Either serve the site "
                "and the API on one origin, or set "
                "STUDYPILOT_AUTH_PROVIDER=neon to authenticate with bearer tokens."
            )
        return warnings

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
