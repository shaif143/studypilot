"""Folders on this machine, indexed in place.

The desktop app lives next to the student's own files. Copying a lecturer's
folder into the app's data directory doubles the disk it takes and leaves
two versions that drift apart; linking the folder instead — the way an
editor opens a project — means the index follows the folder. Each file is
read where it is, nothing is copied (a PowerPoint deck keeps the PDF
rendered from it, which is what the reader shows), and a rescan picks up
what changed: new files indexed, edited ones re-read, deleted ones removed.

A scan runs in a thread of its own with its own database session, because a
folder of recordings takes minutes to transcribe and the student should see
the count climb rather than a frozen dialog. One scan at a time: the local
vector store and the SQLite file are shared with the request handlers, and
serialising writes is cheaper than reasoning about their interleaving.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.ingestion.media import MediaReader, is_audio, is_image, normalise_image, sniff_media_type
from app.ingestion.pipeline import UnsupportedFileError, ingest_document, validate_upload
from app.ingestion.removal import remove_document
from app.runtime import RuntimeContext
from app.storage.models import LocalFolderLink
from app.storage.repositories import (
    ChunkRepository,
    DocumentRepository,
    LocalFolderRepository,
    TopicRepository,
)

log = logging.getLogger(__name__)

#: What a folder scan takes on. Archives are left where they are: the
#: student can upload one from the Library to unpack it.
DOCUMENT_SUFFIXES = frozenset({".pdf", ".pptx", ".ppt"})
MEDIA_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".wav", ".mp3", ".m4a"}
)
INDEXABLE_SUFFIXES = DOCUMENT_SUFFIXES | MEDIA_SUFFIXES
MAX_FILES_PER_SCAN = 500
#: Folders no student wants indexed, however the files inside are named.
_SKIP_DIRS = {"node_modules", ".git", "__MACOSX", ".Trash", "Library", "StudyPilot Notes"}

#: Builds the consent-gated reader for a media type, on the scan's own session.
MediaReaderFactory = Callable[[str, Session], MediaReader]


class FolderError(ValueError):
    """A folder that cannot be linked or read; the message is for the user."""


def resolve_folder(path: str) -> Path:
    """An absolute, existing directory the process can read, or a clear refusal."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise FolderError("Give the folder's full path (it must start with /).")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FolderError(f"That folder does not exist: {exc}") from exc
    if not resolved.is_dir():
        raise FolderError("That path is a file, not a folder.")
    if not os.access(resolved, os.R_OK | os.X_OK):
        raise FolderError("StudyPilot is not allowed to read that folder.")
    if resolved == Path(resolved.anchor) or resolved == Path.home():
        raise FolderError("Link a course folder, not the whole disk or your home folder.")
    return resolved


@dataclass
class FolderFile:
    path: Path
    relative: str
    version: str  # "mtime_ns:size"
    size: int


def list_files(root: Path, *, recursive: bool) -> list[FolderFile]:
    """Indexable files under the folder, sorted by path, hidden ones skipped."""
    found: list[FolderFile] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS)
        if not recursive:
            dirnames[:] = []
        for name in sorted(filenames):
            if name.startswith(".") or name.startswith("~$"):
                continue
            path = Path(dirpath) / name
            if path.suffix.lower() not in INDEXABLE_SUFFIXES or path.is_symlink():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size == 0:
                continue
            found.append(
                FolderFile(
                    path=path,
                    relative=str(path.relative_to(root)),
                    version=f"{stat.st_mtime_ns}:{stat.st_size}",
                    size=stat.st_size,
                )
            )
            if len(found) >= MAX_FILES_PER_SCAN:
                return found
    return found


# --- progress, for the UI to poll ------------------------------------------------------
@dataclass
class ScanProgress:
    state: str = "queued"  # queued | running | done | failed
    #: "documents" while PDFs and decks are read, "media" once images and
    #: recordings start; the slow part comes last.
    phase: str = "documents"
    done: int = 0
    total: int = 0
    current: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    error: str = ""
    finished_at: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "phase": self.phase,
            "done": self.done,
            "total": self.total,
            "current": self.current,
            "counts": dict(self.counts),
            "error": self.error,
        }


_progress: dict[str, ScanProgress] = {}
_scan_lock = threading.Lock()
_registry_lock = threading.Lock()


def progress_for(link_id: str) -> ScanProgress | None:
    """The current or recently finished scan of a link; finished ones linger five minutes."""
    with _registry_lock:
        found = _progress.get(link_id)
        if found and found.state in {"done", "failed"} and time.time() - found.finished_at > 300:
            del _progress[link_id]
            return None
        return found


def _count(progress: ScanProgress, status: str) -> None:
    progress.counts[status] = progress.counts.get(status, 0) + 1


# --- scanning ----------------------------------------------------------------------------
def scan_folder(
    context: RuntimeContext,
    link_id: str,
    *,
    user_id: str,
    media_reader_for: MediaReaderFactory | None = None,
    progress: ScanProgress | None = None,
) -> ScanProgress:
    """Bring a linked folder's course up to date with the folder, in this thread."""
    progress = progress or ScanProgress()
    with _registry_lock:
        _progress[link_id] = progress
    with _scan_lock, context.database.session() as session:
        folders = LocalFolderRepository(session)
        documents = DocumentRepository(session)
        chunks = ChunkRepository(session)
        topics = TopicRepository(session)
        try:
            link = folders.link(user_id, link_id)
            root = resolve_folder(link.path)
            files = order_for_indexing(list_files(root, recursive=link.recursive), link)
            progress.state = "running"
            progress.total = len(files)
            existing = folders.documents_from_folder(user_id, link.course_id)
            seen: set[str] = set()
            refusals: dict[str, str] = {}

            # Phase one, in a moment: a row for every file that will be read,
            # so the Library lists the whole folder at once as "queued" and
            # the rows fill in as the scan reaches them.
            queued: dict[str, object] = {}
            for file in files:
                key = str(file.path)
                previous = existing.get(key)
                if previous is not None and (
                    previous.source_version == file.version
                    and previous.status in {"indexed", "partial"}
                ):
                    continue
                if previous is not None:
                    remove_document(
                        context=context,
                        session=session,
                        documents=documents,
                        chunks=chunks,
                        document=previous,
                    )
                    existing.pop(key, None)
                if will_index(file, link):
                    queued[key] = documents.create(
                        user_id=link.user_id,
                        course_id=link.course_id,
                        title=file.path.stem[:512],
                        original_filename=file.path.name[:512],
                        media_type="application/octet-stream",
                        content_hash="",
                        byte_size=file.size,
                        kind="lecture",
                        status="pending",
                        status_detail="Queued; this folder is being indexed.",
                        source="local_folder",
                        source_ref=key,
                        source_version=file.version,
                        extraction_meta={"folder_path": file.relative},
                    )
            session.commit()

            # Phase two: read them, documents before images and recordings.
            for index, file in enumerate(files):
                progress.done = index
                progress.current = file.relative
                progress.phase = "media" if is_media_file(file) else "documents"
                key = str(file.path)
                seen.add(key)
                row = queued.get(key)
                if row is None:
                    if key in existing:
                        _count(progress, "unchanged")
                    else:
                        _count(progress, "skipped")
                    continue
                status = _index_file(
                    context,
                    session,
                    link,
                    file,
                    row,
                    documents=documents,
                    chunks=chunks,
                    topics=topics,
                    media_reader_for=media_reader_for,
                    refusals=refusals,
                )
                _count(progress, status)
                session.commit()
            for key, document in existing.items():
                if key in seen:
                    continue
                remove_document(
                    context=context,
                    session=session,
                    documents=documents,
                    chunks=chunks,
                    document=document,
                )
                _count(progress, "removed")
                session.commit()
            progress.done = len(files)
            progress.current = ""
            folders.record_scan(link, progress.counts)
            session.commit()
            progress.state = "done"
        except Exception as exc:
            session.rollback()
            log.exception("folder_scan_failed", extra={"link_id": link_id})
            progress.state = "failed"
            progress.error = str(exc)
        finally:
            progress.finished_at = time.time()
    return progress


def is_media_file(file: FolderFile) -> bool:
    return file.path.suffix.lower() in MEDIA_SUFFIXES


def will_index(file: FolderFile, link: LocalFolderLink) -> bool:
    """Documents always; images and recordings only with the link's consent."""
    return not is_media_file(file) or bool(link.read_media)


def order_for_indexing(files: list[FolderFile], link: LocalFolderLink) -> list[FolderFile]:
    """Documents first (seconds each), then the media a model reads (minutes)."""
    return sorted(files, key=lambda f: (is_media_file(f), f.relative))


def _index_file(
    context: RuntimeContext,
    session: Session,
    link: LocalFolderLink,
    file: FolderFile,
    document,
    *,
    documents: DocumentRepository,
    chunks: ChunkRepository,
    topics: TopicRepository,
    media_reader_for: MediaReaderFactory | None,
    refusals: dict[str, str],
) -> str:
    """Index one file where it is; returns the outcome status.

    Documents are read in place. Images and recordings need a model, which
    the link's ``read_media`` consents to; a converted image (GIF to PNG) is
    indexed from the converted bytes, since the file on disk is not what the
    model receives.
    """
    settings = context.settings
    if file.size > settings.max_upload_bytes:
        documents.set_status(document, "failed", "Larger than the upload limit.")
        return "skipped"
    name = file.path.name
    is_media = file.path.suffix.lower() in MEDIA_SUFFIXES
    reader: MediaReader | None = None
    source_bytes: bytes | None = None
    source_path: Path | None = file.path
    try:
        data = file.path.read_bytes()
        if is_media:
            if not link.read_media or media_reader_for is None:
                documents.set_status(document, "failed", "Images and recordings were not included.")
                return "skipped"
            name, data = normalise_image(name, data)
            media = sniff_media_type(data, name) or ""
            if not (is_image(media) or is_audio(media)):
                documents.set_status(document, "failed", "Not a readable image or recording.")
                return "skipped"
            if media not in refusals:
                refusals[media] = media_reader_for(media, session).cannot_read(media)
            if refusals[media]:
                documents.set_status(document, "failed", refusals[media])
                return "skipped"
            reader = media_reader_for(media, session)
            source_bytes, source_path = data, None
        media_type = validate_upload(name, data, settings)
        if media_type == "application/pdf" and link.read_media and media_reader_for is not None:
            # Only used if the PDF turns out to be a scan with no text layer.
            reader = media_reader_for("image/png", session)
        content_hash = context.blobs.hash_bytes(data)
    except (UnsupportedFileError, OSError) as exc:
        documents.set_status(document, "failed", str(exc)[:500])
        return "skipped"
    del data

    document.original_filename = name[:512]
    document.media_type = media_type
    document.content_hash = content_hash
    document.status_detail = ""
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
        media_reader=reader,
        source_bytes=source_bytes,
        source_path=source_path,
        keep_previews=False,
    )
    document.extraction_meta = {**(document.extraction_meta or {}), "folder_path": file.relative}
    return result.status


def start_scan(
    context: RuntimeContext,
    link_id: str,
    *,
    user_id: str,
    media_reader_for: MediaReaderFactory | None,
) -> ScanProgress:
    """Run a scan in the background; returns its progress object at once."""
    current = progress_for(link_id)
    if current is not None and current.state in {"queued", "running"}:
        return current
    progress = ScanProgress()
    with _registry_lock:
        _progress[link_id] = progress
    threading.Thread(
        target=scan_folder,
        args=(context, link_id),
        kwargs={"user_id": user_id, "media_reader_for": media_reader_for, "progress": progress},
        name=f"folder-scan-{link_id[:8]}",
        daemon=True,
    ).start()
    return progress


def read_in_place(folders: LocalFolderRepository, user_id: str, path: str) -> bytes:
    """The bytes of a linked file, only while it still lies inside a linked folder."""
    if not folders.owns_path(user_id, path):
        raise FolderError("That file is no longer inside a linked folder.")
    target = Path(path)
    if not target.is_file():
        raise FolderError("The file has moved or been deleted. Rescan the folder.")
    return target.read_bytes()


# --- writing back ----------------------------------------------------------------------
NOTES_FOLDER_NAME = "StudyPilot Notes"


def safe_filename(title: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in " -_()." else " " for ch in title).strip()
    cleaned = " ".join(cleaned.split())
    return (cleaned or "notes")[:120]


def write_note_to_folder(link: LocalFolderLink, title: str, markdown: str) -> Path:
    """Save a note as Markdown under "StudyPilot Notes" inside the linked folder.

    The one thing the app writes into a folder of the student's, in a
    subfolder of its own, so a rescan can tell its own files from theirs.
    """
    root = resolve_folder(link.path)
    target_dir = root / NOTES_FOLDER_NAME
    try:
        target_dir.mkdir(exist_ok=True)
        target = target_dir / f"{safe_filename(title)}.md"
        target.write_text(markdown, encoding="utf-8")
    except OSError as exc:
        raise FolderError(f"Could not write into the folder: {exc}") from exc
    return target
