"""Folders on this Mac: link one to a course and index it in place.

Desktop only. A hosted deployment runs on a server with none of the
student's files, so the routes answer 404 there and the UI does not offer
the feature. Paths are resolved and checked before anything is read, and a
linked file is served back only while it still lies inside a linked folder.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import ContextDep, LocalFoldersDep, UserDep, require_token
from app.api.routes_documents import _media_reader_for
from app.api.schemas import (
    LocalFolderLinkIn,
    LocalFolderLinkOut,
    LocalFolderScanOut,
    LocalFoldersOut,
)
from app.ingestion import local_folders
from app.storage.models import LocalFolderLink
from app.storage.repositories import CourseRepository, UserRepository

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/local-folders", tags=["local-folders"], dependencies=[Depends(require_token)]
)


def _require_desktop(context: ContextDep) -> None:
    if context.settings.is_hosted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="A hosted deployment has no folder of yours to read; upload files instead.",
        )


def _out(link: LocalFolderLink) -> LocalFolderLinkOut:
    out = LocalFolderLinkOut.model_validate(link)
    progress = local_folders.progress_for(link.id)
    if progress is not None:
        out.scan = LocalFolderScanOut(**progress.to_dict())
    return out


def _reader_factory(context: ContextDep, user_id: str, allow_cloud: bool):
    """A reader builder for the scan thread, on the scan's own session."""

    class _User:
        id = user_id

    def make(kind: str, session):
        return _media_reader_for(kind, allow_cloud, context, _User(), UserRepository(session))

    return make


@router.get("", response_model=LocalFoldersOut)
def list_folders(context: ContextDep, user: UserDep, folders: LocalFoldersDep) -> LocalFoldersOut:
    """Linked folders, each with the progress of a running scan if there is one."""
    if context.settings.is_hosted:
        return LocalFoldersOut(available=False)
    return LocalFoldersOut(available=True, links=[_out(link) for link in folders.links(user.id)])


@router.post("", response_model=LocalFolderLinkOut, status_code=status.HTTP_201_CREATED)
def link_folder(
    body: LocalFolderLinkIn, context: ContextDep, user: UserDep, folders: LocalFoldersDep
) -> LocalFolderLinkOut:
    """Link a folder to a course and start indexing it in place."""
    _require_desktop(context)
    CourseRepository(folders.session).get(user.id, body.course_id)  # ownership
    try:
        resolved = local_folders.resolve_folder(body.path)
    except local_folders.FolderError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    link = folders.link_folder(
        user.id,
        body.course_id,
        path=str(resolved),
        recursive=body.recursive,
        read_media=body.read_media,
    )
    folders.session.commit()  # the scan thread must see the row
    factory = _reader_factory(context, user.id, body.read_media)
    if body.wait:
        local_folders.scan_folder(context, link.id, user_id=user.id, media_reader_for=factory)
    else:
        local_folders.start_scan(context, link.id, user_id=user.id, media_reader_for=factory)
    folders.session.expire_all()
    return _out(folders.link(user.id, link.id))


@router.post("/{link_id}/scan", response_model=LocalFolderLinkOut)
def rescan(
    link_id: str,
    context: ContextDep,
    user: UserDep,
    folders: LocalFoldersDep,
    wait: bool = False,
) -> LocalFolderLinkOut:
    """Re-check the folder: new files indexed, changed ones re-read, gone ones removed."""
    _require_desktop(context)
    link = folders.link(user.id, link_id)
    factory = _reader_factory(context, user.id, link.read_media)
    folders.session.commit()
    if wait:
        local_folders.scan_folder(context, link.id, user_id=user.id, media_reader_for=factory)
    else:
        local_folders.start_scan(context, link.id, user_id=user.id, media_reader_for=factory)
    folders.session.expire_all()
    return _out(folders.link(user.id, link_id))


@router.delete("/{link_id}", response_model=LocalFoldersOut)
def unlink_folder(
    link_id: str, context: ContextDep, user: UserDep, folders: LocalFoldersDep
) -> LocalFoldersOut:
    """Stop indexing a folder and drop the documents that came from it.

    They were only ever an index of files that stay where they are; without
    the link the app may not read those files back, so the index goes too.
    """
    _require_desktop(context)
    from app.ingestion.removal import remove_document
    from app.storage.repositories import ChunkRepository, DocumentRepository

    link = folders.link(user.id, link_id)
    documents = DocumentRepository(folders.session)
    chunks = ChunkRepository(folders.session)
    root = link.path.rstrip("/") + "/"
    for document in list(folders.documents_from_folder(user.id, link.course_id).values()):
        if document.source_ref.startswith(root):
            remove_document(
                context=context,
                session=folders.session,
                documents=documents,
                chunks=chunks,
                document=document,
            )
    folders.unlink(user.id, link_id)
    return LocalFoldersOut(available=True, links=[_out(row) for row in folders.links(user.id)])
