"""The bytes the reader shows for a document, wherever they live.

A PDF upload is in the blob store. A PowerPoint deck is the PDF rendered from
it. A document read from Google Drive or from a folder on this machine keeps
nothing here, so its bytes are fetched from where they are, now. Two callers
need exactly this — the reader's file endpoint and the slide explainer, which
shows the model the slide — so it is decided once.
"""

from __future__ import annotations

from pathlib import Path

from app.runtime import RuntimeContext
from app.storage.files import BlobNotFoundError
from app.storage.models import Document
from app.storage.repositories import DriveRepository, LocalFolderRepository


class OriginalUnavailableError(Exception):
    """The original cannot be read right now; ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def read_original(
    context: RuntimeContext,
    user_id: str,
    document: Document,
    drive: DriveRepository,
    folders: LocalFolderRepository,
) -> tuple[bytes, str]:
    """Return ``(bytes, filename)`` of the file the reader renders for this document."""
    rendered = (document.extraction_meta or {}).get("rendered_pdf", "")
    filename = document.original_filename
    try:
        if rendered:
            return context.blobs.read_asset(rendered), f"{Path(filename).stem}.pdf"
        if document.source == "google_drive":
            from app.integrations.drive_import import fetch_from_drive
            from app.integrations.google_drive import DriveError

            try:
                return fetch_from_drive(context, drive, user_id, document), filename
            except DriveError as exc:
                raise OriginalUnavailableError(exc.status, str(exc)) from exc
        if document.source == "local_folder":
            from app.ingestion.local_folders import FolderError, read_in_place

            try:
                return read_in_place(folders, user_id, document.source_ref), filename
            except FolderError as exc:
                raise OriginalUnavailableError(409, str(exc)) from exc
        return context.blobs.read(document.content_hash), filename
    except BlobNotFoundError as exc:
        raise OriginalUnavailableError(404, "The stored file is missing. Upload it again.") from exc
