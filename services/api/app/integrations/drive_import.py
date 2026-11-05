"""Google Drive in the application: connections, imports and syncs.

`google_drive` speaks to Google; this module decides what the app does with
it — where the token key lives, how a sign-in is started and finished, and
how a Drive file becomes a document whose bytes stay in Drive.

The rule for storage is simple and the same everywhere: a document from
Drive keeps its text, topics, chunks and embeddings here, and nothing else.
No original, no page previews. (A PowerPoint deck is the one exception: the
PDF rendered from it is kept, because that rendering *is* what the reader
shows and there is nothing in Drive to stream instead.)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app import secrets
from app.config import Settings
from app.ingestion.pipeline import ingest_document
from app.ingestion.removal import remove_document
from app.integrations import google_drive as gd
from app.runtime import RuntimeContext
from app.storage.models import Document, DriveConnection, DriveLink
from app.storage.repositories import (
    ChunkRepository,
    DocumentRepository,
    DriveRepository,
    TopicRepository,
)

log = logging.getLogger(__name__)

#: Keychain entry (desktop) holding the key that encrypts stored tokens.
TOKEN_KEY_NAME = "drive_token_key"
CALLBACK_PATH = "/api/integrations/google/callback"
#: How many files one import or sync will take on. A course folder is tens
#: of decks, not thousands; the bound keeps a request from running for hours.
MAX_FILES_PER_RUN = 300


# --- configuration -------------------------------------------------------------
def oauth_client(settings: Settings) -> gd.OAuthClient:
    client = gd.OAuthClient.from_environment()
    if client.configured:
        return client
    if settings.is_hosted:
        raise gd.DriveNotConfiguredError(
            "Google Drive is not set up on this site: its operator has to provide "
            "GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET."
        )
    raise gd.DriveNotConfiguredError(
        "Enter the Google OAuth client id and secret in Settings → Google Drive first."
    )


def client_source(settings: Settings) -> str:
    """Where the OAuth client came from, for the status report."""
    if not gd.OAuthClient.from_environment().configured:
        return "none"
    if settings.is_hosted:
        return "environment"
    status = secrets.status(settings)
    came_from_env = any(
        status.get(name, {}).get("from_environment") for name in secrets.INTEGRATION_KEYS
    )
    return "environment" if came_from_env else "settings"


def cipher_for(settings: Settings) -> gd.TokenCipher:
    """The cipher over stored tokens, with its key where secrets live here."""
    if settings.is_hosted:
        secret = settings.token_encryption_key or settings.jwt_secret
        if not secret:
            raise gd.DriveNotConfiguredError(
                "Set STUDYPILOT_TOKEN_ENCRYPTION_KEY so Google tokens can be stored encrypted."
            )
        return gd.TokenCipher(gd.TokenCipher.key_from_secret(secret))
    key = secrets.get_secret(settings, TOKEN_KEY_NAME)
    if not key:
        key = gd.TokenCipher.new_key().decode("ascii")
        secrets.set_secret(settings, TOKEN_KEY_NAME, key)
    return gd.TokenCipher(key.encode("ascii"))


def redirect_uri_for(settings: Settings, request: Request) -> str:
    """Where Google sends the browser back.

    The desktop's backend is on the loopback with a port chosen per launch;
    Google accepts any loopback port for a *Desktop app* client, so this
    needs no registration. A hosted site's URL must be registered on its
    *Web application* client exactly as built here.
    """
    if not settings.is_hosted:
        return f"http://127.0.0.1:{settings.port}{CALLBACK_PATH}"
    base = settings.public_api_url.rstrip("/") or _origin_of(request)
    return f"{base}{CALLBACK_PATH}"


def return_url_for(settings: Settings, request: Request, outcome: str) -> str:
    """Where a hosted callback sends the browser once the tokens are stored."""
    origin = settings.web_origin.rstrip("/")
    if not origin and settings.effective_cors_origins:
        origin = settings.effective_cors_origins[0]
    if not origin:
        origin = _origin_of(request)
    return f"{origin}/settings?drive={outcome}"


def _origin_of(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = (
        request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    )
    return f"{proto}://{host}"


# --- the connection ---------------------------------------------------------------
def start_connection(
    settings: Settings, drive: DriveRepository, user_id: str, redirect_uri: str
) -> str:
    """Record a flow and return the consent URL for the browser."""
    client = oauth_client(settings)
    state = gd.new_state()
    verifier = gd.new_verifier()
    drive.start_flow(state=state, user_id=user_id, verifier=verifier, redirect_uri=redirect_uri)
    return gd.authorization_url(client, redirect_uri, state, verifier)


def finish_connection(
    settings: Settings, drive: DriveRepository, *, state: str, code: str
) -> DriveConnection:
    """Exchange the code the browser brought back and store the tokens."""
    flow = drive.take_flow(state)
    if flow is None:
        raise gd.DriveError(
            "This sign-in is not one StudyPilot started, or it took too long. Try again.",
            status=400,
        )
    client = oauth_client(settings)
    with gd.http_client() as http:
        tokens = gd.exchange_code(http, client, code, flow.redirect_uri, flow.verifier)
        email = gd.DriveClient(http, tokens.access_token).account_email()
    cipher = cipher_for(settings)
    return drive.save_connection(
        flow.user_id,
        account_email=email,
        scope=tokens.scope,
        token_ciphertext=cipher.encrypt(tokens),
    )


def disconnect(settings: Settings, drive: DriveRepository, user_id: str) -> None:
    """Revoke at Google, then forget the tokens and every linked folder."""
    row = drive.connection(user_id)
    if row is not None and row.token_ciphertext:
        try:
            tokens = cipher_for(settings).decrypt(row.token_ciphertext)
        except gd.DriveError:
            tokens = None
        if tokens is not None:
            with gd.http_client() as http:
                gd.revoke(http, tokens.refresh_token or tokens.access_token)
    drive.delete_connection(user_id)


class ConnectedDrive:
    """A Drive client for a user, refreshing the access token when needed."""

    def __init__(self, settings: Settings, drive: DriveRepository, user_id: str) -> None:
        row = drive.connection(user_id)
        if row is None or not row.token_ciphertext:
            raise gd.DriveNotConnectedError()
        self._settings = settings
        self._drive = drive
        self._row = row
        self._cipher = cipher_for(settings)
        self._tokens = self._cipher.decrypt(row.token_ciphertext)
        self.http = gd.http_client()

    def __enter__(self) -> gd.DriveClient:
        if self._tokens.expired:
            client = oauth_client(self._settings)
            self._tokens = gd.refresh_tokens(self.http, client, self._tokens)
            self._row.token_ciphertext = self._cipher.encrypt(self._tokens)
            self._drive.session.flush()
        return gd.DriveClient(self.http, self._tokens.access_token)

    def __exit__(self, *exc: object) -> None:
        self.http.close()


# --- importing -------------------------------------------------------------------
@dataclass
class ImportOutcome:
    file_id: str
    name: str
    path: str
    status: str  # indexed | partial | failed | skipped | unchanged | removed
    detail: str = ""
    document_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "name": self.name,
            "path": self.path,
            "status": self.status,
            "detail": self.detail,
            "document_id": self.document_id,
        }


@dataclass
class ImportRun:
    outcomes: list[ImportOutcome]
    duration_ms: int

    @property
    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            counts[outcome.status] = counts.get(outcome.status, 0) + 1
        return counts


def import_files(
    *,
    context: RuntimeContext,
    session: Session,
    client: gd.DriveClient,
    user_id: str,
    course_id: str,
    files: list[gd.DriveFile],
    documents: DocumentRepository,
    chunks: ChunkRepository,
    topics: TopicRepository,
    drive: DriveRepository,
    remove_missing: bool = False,
) -> ImportRun:
    """Index Drive files into a course, keeping no copy of any of them.

    A file already indexed from Drive is skipped unless its modification
    time moved, in which case the old document is removed and the file read
    again. With ``remove_missing`` (a linked-folder sync) documents whose
    file is no longer in the folder are removed too.
    """
    started = time.monotonic()
    settings = context.settings
    existing = drive.documents_from_drive(user_id, course_id)
    outcomes: list[ImportOutcome] = []
    seen: set[str] = set()

    for file in files[:MAX_FILES_PER_RUN]:
        seen.add(file.id)
        if not file.importable:
            outcomes.append(
                ImportOutcome(file.id, file.name, file.path, "skipped", file.why_skipped)
            )
            continue
        previous = existing.get(file.id)
        if previous is not None:
            if previous.source_version == file.modified_time and previous.status in {
                "indexed",
                "partial",
            }:
                outcomes.append(
                    ImportOutcome(file.id, file.name, file.path, "unchanged", "", previous.id)
                )
                continue
            remove_document(
                context=context,
                session=session,
                documents=documents,
                chunks=chunks,
                document=previous,
            )

        try:
            data = client.fetch(file, max_bytes=settings.max_upload_bytes)
        except gd.DriveError as exc:
            outcomes.append(ImportOutcome(file.id, file.name, file.path, "failed", str(exc)))
            continue

        content_hash = context.blobs.hash_bytes(data)
        document = documents.create(
            user_id=user_id,
            course_id=course_id,
            title=(file.name.rsplit(".", 1)[0] if "." in file.name else file.name)[:512],
            original_filename=file.imported_filename[:512],
            media_type=file.imported_media_type,
            content_hash=content_hash,
            byte_size=len(data),
            kind="lecture",
            status="pending",
            source="google_drive",
            source_ref=file.id,
            source_version=file.modified_time,
        )
        result = ingest_document(
            document=document,
            settings=settings,
            blobs=context.blobs,
            embedder=context.embedder,
            vector_store=context.vector_store,
            sparse_index=context.sparse_index,
            documents=documents,
            chunks=chunks,
            topics=topics,
            source_bytes=data,
            keep_previews=False,
        )
        del data
        document.extraction_meta = {
            **(document.extraction_meta or {}),
            "drive_path": file.path,
            "drive_mime_type": file.mime_type,
        }
        detail = "; ".join(result.warnings) if result.status != "indexed" else ""
        outcomes.append(
            ImportOutcome(file.id, file.name, file.path, result.status, detail, document.id)
        )

    if remove_missing:
        for file_id, document in existing.items():
            if file_id in seen:
                continue
            name = document.original_filename
            remove_document(
                context=context,
                session=session,
                documents=documents,
                chunks=chunks,
                document=document,
            )
            outcomes.append(
                ImportOutcome(file_id, name, "", "removed", "No longer in the Drive folder.")
            )

    return ImportRun(outcomes=outcomes, duration_ms=int((time.monotonic() - started) * 1000))


def sync_link(
    *,
    context: RuntimeContext,
    session: Session,
    client: gd.DriveClient,
    link: DriveLink,
    documents: DocumentRepository,
    chunks: ChunkRepository,
    topics: TopicRepository,
    drive: DriveRepository,
) -> ImportRun:
    """Bring a linked folder's course up to date with the folder."""
    files = client.walk(link.folder_id, recursive=link.recursive, max_files=MAX_FILES_PER_RUN)
    run = import_files(
        context=context,
        session=session,
        client=client,
        user_id=link.user_id,
        course_id=link.course_id,
        files=files,
        documents=documents,
        chunks=chunks,
        topics=topics,
        drive=drive,
        remove_missing=True,
    )
    drive.record_sync(link, run.counts)
    return run


def fetch_from_drive(
    context: RuntimeContext, drive: DriveRepository, user_id: str, document: Document
) -> bytes:
    """The bytes of a Drive-sourced document, read now, kept nowhere."""
    with ConnectedDrive(context.settings, drive, user_id) as client:
        file = client.file(document.source_ref)
        return client.fetch(file, max_bytes=context.settings.max_upload_bytes)
