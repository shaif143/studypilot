"""The Claude chat: an account's key, its projects, its conversations.

A message is answered as a stream of JSON lines, one event per line, so the
reply appears as it is written. Everything else is ordinary request and
response.

The chat sends what the student types and attaches to Anthropic, and its
tools can read their course material, so it is behind the same two switches
as every other cloud feature — the operator's and the account's — plus a key
of the account's own.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response, StreamingResponse

from app.api.deps import AssistantDep, ContextDep, UserDep, UsersDep, require_token
from app.api.schemas import (
    AssistantFileOut,
    AssistantKeyIn,
    AssistantKeyOut,
    AssistantMessageIn,
    AssistantModelOut,
    AssistantProjectCreate,
    AssistantProjectDetailOut,
    AssistantProjectOut,
    AssistantProjectUpdate,
    AssistantStatusOut,
    AssistantThreadCreate,
    AssistantThreadOut,
    AssistantThreadSummaryOut,
    AssistantThreadUpdate,
)
from app.assistant import keys, service
from app.storage.files import BlobNotFoundError
from app.storage.models import AssistantProject

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/assistant", tags=["assistant"], dependencies=[Depends(require_token)]
)


def _status(context: ContextDep, user_id: str, users: UsersDep, repo: AssistantDep):
    settings = context.settings
    resolved = keys.resolve(repo, settings, user_id)
    installation = settings.cloud_processing_permitted
    account = bool(users.settings(user_id).allow_cloud_processing)
    return AssistantStatusOut(
        key=AssistantKeyOut(
            configured=resolved is not None,
            source=resolved.source if resolved else "",
            hint=resolved.hint if resolved else "",
        ),
        models=[AssistantModelOut(**model) for model in service.MODELS],
        default_model=service.DEFAULT_MODEL,
        installation_allows_cloud=installation,
        account_allows_cloud=account,
        ready=installation and account and resolved is not None,
        max_upload_bytes=settings.max_upload_bytes,
    )


def _require_cloud(context: ContextDep, user_id: str, users: UsersDep) -> None:
    """Refuse unless both switches allow content to leave for a cloud model."""
    if not context.settings.cloud_processing_permitted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cloud AI is switched off for this site by its operator.",
        )
    if not users.settings(user_id).allow_cloud_processing:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Allow cloud AI processing in Settings to use the chat.",
        )


# --- status and the key --------------------------------------------------------
@router.get("", response_model=AssistantStatusOut)
def assistant_status(
    context: ContextDep, user: UserDep, users: UsersDep, repo: AssistantDep
) -> AssistantStatusOut:
    return _status(context, user.id, users, repo)


@router.put("/key", response_model=AssistantStatusOut)
def save_key(
    payload: AssistantKeyIn,
    context: ContextDep,
    user: UserDep,
    users: UsersDep,
    repo: AssistantDep,
) -> AssistantStatusOut:
    """Save this account's Claude API key, after Anthropic confirms it works."""
    try:
        value = keys.clean(payload.key)
        service.check_key(value)
        keys.save(repo, context.settings, user.id, value)
    except keys.KeyStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    log.info("assistant_key_saved", extra={"user_id": user.id})
    return _status(context, user.id, users, repo)


@router.delete("/key", response_model=AssistantStatusOut)
def delete_key(
    context: ContextDep, user: UserDep, users: UsersDep, repo: AssistantDep
) -> AssistantStatusOut:
    keys.delete(repo, user.id)
    return _status(context, user.id, users, repo)


# --- projects --------------------------------------------------------------------
def _project_out(project: AssistantProject, counts: dict[str, dict[str, int]]):
    tally = counts.get(project.id, {})
    return AssistantProjectOut(
        id=project.id,
        name=project.name,
        description=project.description,
        instructions=project.instructions,
        thread_count=tally.get("threads", 0),
        file_count=tally.get("files", 0),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _project_detail(user_id: str, project: AssistantProject, repo: AssistantDep):
    files = repo.project_files(user_id, project.id)
    threads = repo.threads(user_id, project_id=project.id)
    base = _project_out(project, {project.id: {"threads": len(threads), "files": len(files)}})
    return AssistantProjectDetailOut(
        **base.model_dump(),
        files=[AssistantFileOut.model_validate(row) for row in files],
        threads=[AssistantThreadSummaryOut(**service.describe_summary(row)) for row in threads],
    )


@router.get("/projects", response_model=list[AssistantProjectOut])
def list_projects(user: UserDep, repo: AssistantDep) -> list[AssistantProjectOut]:
    counts = repo.project_counts(user.id)
    return [_project_out(project, counts) for project in repo.projects(user.id)]


@router.post(
    "/projects", response_model=AssistantProjectDetailOut, status_code=status.HTTP_201_CREATED
)
def create_project(
    payload: AssistantProjectCreate, user: UserDep, repo: AssistantDep
) -> AssistantProjectDetailOut:
    name = " ".join(payload.name.split())
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Give it a name.")
    project = repo.create_project(
        user.id,
        name=name,
        description=payload.description.strip(),
        instructions=payload.instructions.strip(),
    )
    return _project_detail(user.id, project, repo)


@router.get("/projects/{project_id}", response_model=AssistantProjectDetailOut)
def get_project(project_id: str, user: UserDep, repo: AssistantDep) -> AssistantProjectDetailOut:
    return _project_detail(user.id, repo.project(user.id, project_id), repo)


@router.patch("/projects/{project_id}", response_model=AssistantProjectDetailOut)
def update_project(
    project_id: str, payload: AssistantProjectUpdate, user: UserDep, repo: AssistantDep
) -> AssistantProjectDetailOut:
    project = repo.project(user.id, project_id)
    if payload.name is not None:
        name = " ".join(payload.name.split())
        if not name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Give it a name.")
        project.name = name
    if payload.description is not None:
        project.description = payload.description.strip()
    if payload.instructions is not None:
        project.instructions = payload.instructions.strip()
    repo.session.flush()
    return _project_detail(user.id, project, repo)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, context: ContextDep, user: UserDep, repo: AssistantDep) -> None:
    """Remove a project and its files. Its conversations are kept, outside any project."""
    service.remove_files(context, repo.delete_project(user.id, project_id))


@router.post(
    "/projects/{project_id}/files",
    response_model=AssistantFileOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_project_file(
    project_id: str,
    context: ContextDep,
    user: UserDep,
    repo: AssistantDep,
    file: UploadFile = File(...),
) -> AssistantFileOut:
    """Add a file every conversation in the project can see."""
    project = repo.project(user.id, project_id)
    if len(repo.project_files(user.id, project.id)) >= service.MAX_PROJECT_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A project holds up to {service.MAX_PROJECT_FILES} files.",
        )
    data = await file.read()
    row = service.store_file(
        context,
        repo,
        user_id=user.id,
        filename=file.filename or "file",
        data=data,
        project_id=project.id,
    )
    return AssistantFileOut.model_validate(row)


# --- conversations -----------------------------------------------------------------
def _thread_out(user_id: str, thread_id: str, repo: AssistantDep) -> AssistantThreadOut:
    thread = repo.thread(user_id, thread_id)
    attachments = {row.id: row for row in repo.attachments(user_id, thread.id)}
    return AssistantThreadOut(**service.describe_thread(thread, attachments))


@router.get("/threads", response_model=list[AssistantThreadSummaryOut])
def list_threads(
    user: UserDep, repo: AssistantDep, project_id: str | None = None
) -> list[AssistantThreadSummaryOut]:
    return [
        AssistantThreadSummaryOut(**service.describe_summary(row))
        for row in repo.threads(user.id, project_id=project_id)
    ]


@router.post("/threads", response_model=AssistantThreadOut, status_code=status.HTTP_201_CREATED)
def create_thread(
    payload: AssistantThreadCreate, user: UserDep, repo: AssistantDep
) -> AssistantThreadOut:
    project_id = None
    if payload.project_id:
        project_id = repo.project(user.id, payload.project_id).id
    model = payload.model if payload.model in service.MODEL_IDS else service.DEFAULT_MODEL
    thread = repo.create_thread(user.id, model=model, project_id=project_id)
    return _thread_out(user.id, thread.id, repo)


@router.get("/threads/{thread_id}", response_model=AssistantThreadOut)
def get_thread(thread_id: str, user: UserDep, repo: AssistantDep) -> AssistantThreadOut:
    return _thread_out(user.id, thread_id, repo)


@router.patch("/threads/{thread_id}", response_model=AssistantThreadOut)
def update_thread(
    thread_id: str, payload: AssistantThreadUpdate, user: UserDep, repo: AssistantDep
) -> AssistantThreadOut:
    """Rename a conversation, or move it into or out of a project."""
    thread = repo.thread(user.id, thread_id)
    if payload.title is not None:
        thread.title = " ".join(payload.title.split())[:200] or thread.title
    if payload.project_id is not None:
        if service.is_running(thread.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Wait for the reply to finish before moving this chat.",
            )
        thread.project_id = (
            repo.project(user.id, payload.project_id).id if payload.project_id else None
        )
    repo.session.flush()
    return _thread_out(user.id, thread.id, repo)


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_thread(thread_id: str, context: ContextDep, user: UserDep, repo: AssistantDep) -> None:
    service.stop(thread_id)
    service.remove_files(context, repo.delete_thread(user.id, thread_id))


@router.post(
    "/threads/{thread_id}/attachments",
    response_model=AssistantFileOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_attachment(
    thread_id: str,
    context: ContextDep,
    user: UserDep,
    repo: AssistantDep,
    file: UploadFile = File(...),
) -> AssistantFileOut:
    """Attach a file or photo, ahead of the message that will carry it."""
    thread = repo.thread(user.id, thread_id)
    data = await file.read()
    row = service.store_file(
        context,
        repo,
        user_id=user.id,
        filename=file.filename or "file",
        data=data,
        thread_id=thread.id,
    )
    return AssistantFileOut.model_validate(row)


@router.get("/attachments/{attachment_id}/content")
def attachment_content(
    attachment_id: str, context: ContextDep, user: UserDep, repo: AssistantDep
) -> Response:
    row = repo.attachment(user.id, attachment_id)
    try:
        data = context.blobs.read_asset(row.asset_key)
    except (BlobNotFoundError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That file is no longer stored."
        ) from exc
    # Only images are drawn in the page; anything else is offered as a download,
    # so an attached HTML file is never run as a page of this site.
    inline = row.kind == "image"
    return Response(
        content=data,
        media_type=row.media_type if inline else "application/octet-stream",
        headers={
            "Content-Disposition": "inline" if inline else "attachment",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attachment(
    attachment_id: str, context: ContextDep, user: UserDep, repo: AssistantDep
) -> None:
    """Remove a project file, or an attachment that has not been sent yet."""
    row = repo.attachment(user.id, attachment_id)
    if row.thread_id:
        thread = repo.thread(user.id, row.thread_id)
        sent = any(
            block.get("type") == "sp_attachment" and block.get("id") == row.id
            for message in (thread.messages or [])
            if isinstance(message.get("content"), list)
            for block in message["content"]
        )
        if sent:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That file is part of the conversation. Delete the chat to remove it.",
            )
    service.remove_files(context, [repo.delete_attachment(user.id, attachment_id)])


@router.post("/threads/{thread_id}/messages")
def send_message(
    thread_id: str,
    payload: AssistantMessageIn,
    context: ContextDep,
    user: UserDep,
    users: UsersDep,
    repo: AssistantDep,
) -> StreamingResponse:
    """Send a message and stream the reply, one JSON event per line."""
    _require_cloud(context, user.id, users)
    thread = repo.thread(user.id, thread_id)
    text = payload.text.strip()
    if not text and not payload.attachment_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Write a message.")
    model = payload.model or thread.model or service.DEFAULT_MODEL
    if model not in service.MODEL_IDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="That model is not offered here."
        )
    if keys.resolve(repo, context.settings, user.id) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Save a Claude API key first."
        )
    stopped = service.begin(thread.id)
    if stopped is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A reply is still being written in this chat. Stop it first.",
        )
    user_id = user.id

    def lines() -> Iterator[bytes]:
        service.mark_started(thread_id, stopped)
        try:
            for event in service.run_turn(
                context,
                user_id=user_id,
                thread_id=thread_id,
                text=text,
                attachment_ids=list(payload.attachment_ids),
                model=model,
                stopped=stopped,
            ):
                yield (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
        except Exception:
            # The response has started, so this cannot become a status code.
            # Say so in the stream; the detail stays in the log.
            log.exception("assistant_turn_failed", extra={"thread_id": thread_id})
            failure = {"type": "error", "message": "The reply stopped unexpectedly. Send again."}
            yield (json.dumps(failure) + "\n").encode("utf-8")
        finally:
            service.end(thread_id, stopped)

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/threads/{thread_id}/stop", status_code=status.HTTP_204_NO_CONTENT)
def stop_message(thread_id: str, user: UserDep, repo: AssistantDep) -> None:
    """Stop the reply being written. What was finished so far is kept."""
    repo.thread(user.id, thread_id)  # ownership
    service.stop(thread_id)
