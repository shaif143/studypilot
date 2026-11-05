"""Google Drive: connect an account, browse it, index a folder, keep it synced.

Everything here reads Drive; nothing writes to it. The callback is the one
route without the local token, because Google's redirect cannot carry it —
what it carries instead is the one-time `state` that ties it to a sign-in
this process started for a known user.
"""

from __future__ import annotations

import html
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.deps import (
    ChunksDep,
    ContextDep,
    CoursesDep,
    DocumentsDep,
    DriveDep,
    SessionDep,
    TopicsDep,
    UserDep,
    require_token,
)
from app.api.schemas import (
    DriveConnectOut,
    DriveConnectRequest,
    DriveFileOut,
    DriveImportOut,
    DriveImportOutcomeOut,
    DriveImportRequest,
    DriveLinkOut,
    DriveListingOut,
    GoogleDriveStatusOut,
)
from app.integrations import drive_import
from app.integrations import google_drive as gd

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/integrations/google",
    tags=["integrations"],
    dependencies=[Depends(require_token)],
)
public_router = APIRouter(tags=["integrations"])


def _status(context: ContextDep, drive: DriveDep, user_id: str) -> GoogleDriveStatusOut:
    settings = context.settings
    connection = drive.connection(user_id)
    return GoogleDriveStatusOut(
        configured=gd.OAuthClient.from_environment().configured,
        client_source=drive_import.client_source(settings),  # type: ignore[arg-type]
        editable=not settings.is_hosted,
        connected=connection is not None and bool(connection.token_ciphertext),
        account_email=connection.account_email if connection else "",
        links=[DriveLinkOut.model_validate(link) for link in drive.links(user_id)],
    )


def _raise(exc: gd.DriveError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


@router.get("", response_model=GoogleDriveStatusOut)
def google_status(context: ContextDep, user: UserDep, drive: DriveDep) -> GoogleDriveStatusOut:
    """Whether Drive is set up and connected, and which folders are linked. Never a token."""
    return _status(context, drive, user.id)


@router.post("/connect", response_model=DriveConnectOut)
def connect(
    body: DriveConnectRequest,
    request: Request,
    context: ContextDep,
    user: UserDep,
    drive: DriveDep,
) -> DriveConnectOut:
    """Start the sign-in. On the desktop the consent page opens in the default browser."""
    settings = context.settings
    redirect_uri = drive_import.redirect_uri_for(settings, request)
    try:
        url = drive_import.start_connection(settings, drive, user.id, redirect_uri)
    except gd.DriveError as exc:
        raise _raise(exc) from exc
    if body.open_browser and not settings.is_hosted:
        try:
            gd.open_in_browser(url)
        except Exception as exc:  # pragma: no cover - platform dependent
            log.warning("open_browser_failed", extra={"detail": str(exc)})
    return DriveConnectOut(auth_url=url, redirect_uri=redirect_uri)


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>StudyPilot</title>
<style>
body{{font:16px/1.5 -apple-system,system-ui,sans-serif;background:#0f1117;color:#e6e8ef;
display:grid;place-items:center;min-height:100vh;margin:0}}
main{{max-width:28rem;padding:2rem;text-align:center}}
h1{{font-size:1.25rem;margin:0 0 .5rem}} p{{margin:0;color:#a3a9b8}}
</style></head><body><main><h1>{title}</h1><p>{detail}</p></main></body></html>"""


@public_router.get(drive_import.CALLBACK_PATH, include_in_schema=False, response_model=None)
def callback(
    request: Request,
    context: ContextDep,
    session: SessionDep,
    state: str = "",
    code: str = "",
    error: str = "",
) -> HTMLResponse | RedirectResponse:
    """Google sends the browser here. No token: the state proves which sign-in this is."""
    from app.storage.repositories import DriveRepository

    settings = context.settings
    drive = DriveRepository(session)
    if error or not code or not state:
        outcome, title = "cancelled", "Google Drive was not connected"
        detail = (
            "You can close this tab and return to StudyPilot."
            if error
            else "The sign-in came back incomplete."
        )
    else:
        try:
            connection = drive_import.finish_connection(settings, drive, state=state, code=code)
            session.commit()
            outcome, title, detail = (
                "connected",
                "Google Drive is connected",
                f"StudyPilot can now read {html.escape(connection.account_email)}. "
                "You can close this tab and return to StudyPilot.",
            )
        except gd.DriveError as exc:
            outcome, title = "failed", "Google Drive was not connected"
            detail = html.escape(str(exc))
    if settings.is_hosted:
        return RedirectResponse(
            drive_import.return_url_for(settings, request, outcome),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return HTMLResponse(_PAGE.format(title=html.escape(title), detail=detail))


@router.delete("", response_model=GoogleDriveStatusOut)
def disconnect(context: ContextDep, user: UserDep, drive: DriveDep) -> GoogleDriveStatusOut:
    """Revoke the tokens at Google and forget them and every linked folder. Documents stay."""
    drive_import.disconnect(context.settings, drive, user.id)
    return _status(context, drive, user.id)


@router.get("/files", response_model=DriveListingOut)
def files(
    context: ContextDep, user: UserDep, drive: DriveDep, folder: str = "root"
) -> DriveListingOut:
    """What a folder holds. `folder` is an id, a Drive link, or "root" for My Drive."""
    try:
        folder_id = gd.folder_id_from(folder)
        with drive_import.ConnectedDrive(context.settings, drive, user.id) as client:
            listing = client.listing(folder_id)
    except gd.DriveError as exc:
        raise _raise(exc) from exc
    return DriveListingOut(
        folder=DriveFileOut(**listing.folder.to_dict()),
        parent_id=listing.parent_id,
        files=[DriveFileOut(**file.to_dict()) for file in listing.files],
    )


def _run_out(course_id: str, run: drive_import.ImportRun, link) -> DriveImportOut:
    return DriveImportOut(
        course_id=course_id,
        outcomes=[DriveImportOutcomeOut(**outcome.to_dict()) for outcome in run.outcomes],
        counts=run.counts,
        link=DriveLinkOut.model_validate(link) if link is not None else None,
        duration_ms=run.duration_ms,
    )


@router.post("/import", response_model=DriveImportOut)
def import_from_drive(
    body: DriveImportRequest,
    context: ContextDep,
    session: SessionDep,
    user: UserDep,
    drive: DriveDep,
    courses: CoursesDep,
    documents: DocumentsDep,
    chunks: ChunksDep,
    topics: TopicsDep,
) -> DriveImportOut:
    """Index a folder, or chosen files, into a course — keeping only the index here."""
    courses.get(user.id, body.course_id)  # ownership
    if not body.folder and not body.file_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Choose a folder or some files."
        )
    try:
        with drive_import.ConnectedDrive(context.settings, drive, user.id) as client:
            link = None
            if body.folder:
                folder_id = gd.folder_id_from(body.folder)
                folder, _parent = client.folder(folder_id)
                if body.file_ids:
                    chosen = [client.file(file_id) for file_id in body.file_ids]
                else:
                    chosen = client.walk(
                        folder.id,
                        recursive=body.recursive,
                        max_files=drive_import.MAX_FILES_PER_RUN,
                    )
                if body.link:
                    link = drive.link_folder(
                        user.id,
                        body.course_id,
                        folder_id=folder.id,
                        folder_name=folder.name,
                        recursive=body.recursive,
                    )
            else:
                chosen = [client.file(file_id) for file_id in body.file_ids]
            run = drive_import.import_files(
                context=context,
                session=session,
                client=client,
                user_id=user.id,
                course_id=body.course_id,
                files=chosen,
                documents=documents,
                chunks=chunks,
                topics=topics,
                drive=drive,
            )
            if link is not None:
                drive.record_sync(link, run.counts)
    except gd.DriveError as exc:
        raise _raise(exc) from exc
    return _run_out(body.course_id, run, link)


@router.post("/links/{link_id}/sync", response_model=DriveImportOut)
def sync(
    link_id: str,
    context: ContextDep,
    session: SessionDep,
    user: UserDep,
    drive: DriveDep,
    documents: DocumentsDep,
    chunks: ChunksDep,
    topics: TopicsDep,
) -> DriveImportOut:
    """Re-check a linked folder: new files indexed, changed ones re-read, gone ones removed."""
    link = drive.link(user.id, link_id)
    try:
        with drive_import.ConnectedDrive(context.settings, drive, user.id) as client:
            run = drive_import.sync_link(
                context=context,
                session=session,
                client=client,
                link=link,
                documents=documents,
                chunks=chunks,
                topics=topics,
                drive=drive,
            )
    except gd.DriveError as exc:
        raise _raise(exc) from exc
    return _run_out(link.course_id, run, link)


@router.delete("/links/{link_id}", response_model=GoogleDriveStatusOut)
def unlink(
    link_id: str, context: ContextDep, user: UserDep, drive: DriveDep
) -> GoogleDriveStatusOut:
    """Stop syncing a folder. Documents already indexed from it stay."""
    drive.unlink(user.id, link_id)
    return _status(context, drive, user.id)
