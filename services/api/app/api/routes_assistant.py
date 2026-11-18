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

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse

from app.api.deps import AssistantDep, ContextDep, UserDep, UsersDep, require_token
from app.api.schemas import (
    AssistantCommandOut,
    AssistantConnectorCreate,
    AssistantConnectorOut,
    AssistantConnectorSignInOut,
    AssistantConnectorTestOut,
    AssistantConnectorUpdate,
    AssistantExtensionsOut,
    AssistantFileOut,
    AssistantKeyIn,
    AssistantKeyOut,
    AssistantMessageIn,
    AssistantModelOut,
    AssistantPluginOut,
    AssistantPluginUpdate,
    AssistantProjectCreate,
    AssistantProjectDetailOut,
    AssistantProjectOut,
    AssistantProjectUpdate,
    AssistantSkillOut,
    AssistantStatusOut,
    AssistantThreadCreate,
    AssistantThreadOut,
    AssistantThreadSummaryOut,
    AssistantThreadUpdate,
)
from app.assistant import connector_auth, keys, plugins, service
from app.storage.files import BlobNotFoundError
from app.storage.models import AssistantConnector, AssistantPlugin, AssistantProject

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/assistant", tags=["assistant"], dependencies=[Depends(require_token)]
)
#: Reached by a browser coming back from a sign-in, or by a connector's
#: authorisation server: neither carries this application's credentials.
public_router = APIRouter(tags=["assistant"])


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


# --- connectors and plugins ------------------------------------------------------
def _connector_out(row: AssistantConnector) -> AssistantConnectorOut:
    return AssistantConnectorOut(
        id=row.id,
        name=row.name,
        label=row.label,
        url=row.url,
        has_token=bool(row.token_ciphertext),
        signed_in=bool(row.signed_in),
        enabled=row.enabled,
        plugin_id=row.plugin_id,
        created_at=row.created_at,
    )


def _plugin_out(row: AssistantPlugin) -> AssistantPluginOut:
    prefix = plugins.slug(row.name, "plugin")
    return AssistantPluginOut(
        id=row.id,
        name=row.name,
        version=row.version,
        description=row.description,
        enabled=row.enabled,
        skills=[
            AssistantSkillOut(
                name=f"{prefix}:{skill['name']}",
                description=skill.get("description", ""),
                file_count=len(skill.get("files") or {}),
            )
            for skill in row.skills or []
        ],
        commands=[
            AssistantCommandOut(
                name=f"{prefix}:{command['name']}",
                description=command.get("description", ""),
                plugin=row.name,
            )
            for command in row.commands or []
        ],
        notes=[str(note) for note in row.notes or []],
        created_at=row.created_at,
    )


def _extensions(user_id: str, repo: AssistantDep) -> AssistantExtensionsOut:
    installed = [_plugin_out(row) for row in repo.plugins(user_id)]
    return AssistantExtensionsOut(
        connectors=[_connector_out(row) for row in repo.connectors(user_id)],
        plugins=installed,
        commands=[command for plugin in installed if plugin.enabled for command in plugin.commands],
        max_connectors=service.MAX_CONNECTORS,
    )


def _free_name(user_id: str, repo: AssistantDep, label: str) -> str:
    """A name for the API that no other connector of this account has."""
    taken = {row.name for row in repo.connectors(user_id)}
    base = plugins.slug(label, "connector")
    name, number = base, 2
    while name in taken:
        name = f"{base[:44]}-{number}"
        number += 1
    return name


def _require_room(user_id: str, repo: AssistantDep, *, adding: int = 1) -> None:
    switched_on = len(repo.connectors(user_id, enabled_only=True))
    if switched_on + adding > service.MAX_CONNECTORS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Up to {service.MAX_CONNECTORS} connectors can be switched on at once. "
                "Switch one off first."
            ),
        )


@router.get("/extensions", response_model=AssistantExtensionsOut)
def list_extensions(user: UserDep, repo: AssistantDep) -> AssistantExtensionsOut:
    """The connectors and plugins this account has added to its chat."""
    return _extensions(user.id, repo)


@router.post(
    "/connectors", response_model=AssistantExtensionsOut, status_code=status.HTTP_201_CREATED
)
def add_connector(
    payload: AssistantConnectorCreate, context: ContextDep, user: UserDep, repo: AssistantDep
) -> AssistantExtensionsOut:
    """Connect a remote MCP server. Anthropic reaches it; this site only keeps the details."""
    _require_room(user.id, repo)
    label = " ".join(payload.label.split())
    token = payload.token.strip()
    try:
        ciphertext = keys.seal(context.settings, token) if token else ""
    except keys.KeyStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    repo.add_connector(
        user_id=user.id,
        name=_free_name(user.id, repo, label),
        label=label,
        url=plugins.check_url(payload.url),
        token_ciphertext=ciphertext,
        enabled=True,
    )
    return _extensions(user.id, repo)


@router.patch("/connectors/{connector_id}", response_model=AssistantExtensionsOut)
def update_connector(
    connector_id: str,
    payload: AssistantConnectorUpdate,
    context: ContextDep,
    user: UserDep,
    repo: AssistantDep,
) -> AssistantExtensionsOut:
    row = repo.connector(user.id, connector_id)
    if payload.label is not None:
        row.label = " ".join(payload.label.split()) or row.label
    if payload.url is not None:
        url = plugins.check_url(payload.url)
        if url != row.url:
            # A sign-in was to the old address; its token is not for the new one.
            row.oauth_ciphertext, row.oauth_state, row.token_ciphertext = "", None, ""
            row.signed_in = False
        row.url = url
    if payload.token is not None:
        # A pasted token takes the place of a sign-in, or of no token at all.
        row.signed_in = False
        token = payload.token.strip()
        try:
            row.token_ciphertext = keys.seal(context.settings, token) if token else ""
        except keys.KeyStorageError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
    if payload.enabled is not None and payload.enabled != row.enabled:
        if payload.enabled:
            _require_room(user.id, repo)
        row.enabled = payload.enabled
    repo.session.flush()
    return _extensions(user.id, repo)


@router.delete("/connectors/{connector_id}", response_model=AssistantExtensionsOut)
def delete_connector(
    connector_id: str, user: UserDep, repo: AssistantDep
) -> AssistantExtensionsOut:
    repo.delete_connector(user.id, connector_id)
    return _extensions(user.id, repo)


@router.post("/connectors/{connector_id}/test", response_model=AssistantConnectorTestOut)
def test_connector(
    connector_id: str,
    context: ContextDep,
    user: UserDep,
    users: UsersDep,
    repo: AssistantDep,
) -> AssistantConnectorTestOut:
    """Ask Anthropic to reach the connector, and report the tools it offers.

    A real, small request on the account's key: it is the only way to know
    that the address answers and the token is accepted before a chat depends
    on it.
    """
    _require_cloud(context, user.id, users)
    row = repo.connector(user.id, connector_id)
    resolved = keys.resolve(repo, context.settings, user.id)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Save a Claude API key first."
        )
    try:
        tools = service.probe_connector(
            resolved.value,
            name=row.name,
            url=row.url,
            token=service.connector_token(context.settings, row),
        )
    except ValueError as exc:
        return AssistantConnectorTestOut(ok=False, detail=str(exc))
    return AssistantConnectorTestOut(ok=True, tools=tools)


@router.post("/plugins", response_model=AssistantExtensionsOut, status_code=status.HTTP_201_CREATED)
async def add_plugin(
    user: UserDep, repo: AssistantDep, file: UploadFile = File(...)
) -> AssistantExtensionsOut:
    """Add a plugin from its archive: its skills, its commands, its remote connectors."""
    data = await file.read()
    parsed = plugins.parse(file.filename or "plugin.zip", data)
    row = repo.add_plugin(
        user_id=user.id,
        name=parsed.name,
        version=parsed.version,
        description=parsed.description,
        enabled=True,
        skills=parsed.skills,
        commands=parsed.commands,
        notes=parsed.notes,
    )
    for connector in parsed.connectors:
        # Switched off to begin with: one that needs a token would stop every
        # chat until it had one, so it is for the student to turn on.
        repo.add_connector(
            user_id=user.id,
            name=_free_name(user.id, repo, connector["label"]),
            label=connector["label"],
            url=connector["url"],
            token_ciphertext="",
            enabled=False,
            plugin_id=row.id,
        )
    return _extensions(user.id, repo)


@router.patch("/plugins/{plugin_id}", response_model=AssistantExtensionsOut)
def update_plugin(
    plugin_id: str, payload: AssistantPluginUpdate, user: UserDep, repo: AssistantDep
) -> AssistantExtensionsOut:
    repo.plugin(user.id, plugin_id).enabled = payload.enabled
    repo.session.flush()
    return _extensions(user.id, repo)


@router.delete("/plugins/{plugin_id}", response_model=AssistantExtensionsOut)
def delete_plugin(plugin_id: str, user: UserDep, repo: AssistantDep) -> AssistantExtensionsOut:
    """Remove a plugin, with the connectors that came with it."""
    repo.delete_plugin(user.id, plugin_id)
    return _extensions(user.id, repo)


# --- signing in to a connector ---------------------------------------------------
def _api_origin(context: ContextDep, request: Request) -> str:
    settings = context.settings
    if not settings.is_hosted:
        # The loopback address a browser on this machine can come back to.
        return f"http://127.0.0.1:{settings.port}"
    if settings.public_api_url:
        return settings.public_api_url.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    return f"{proto}://{host or request.url.netloc}"


def _return_origin(context: ContextDep, request: Request) -> str:
    """The site to send the browser back to, if the request came from one we know."""
    settings = context.settings
    origin = (request.headers.get("origin") or "").rstrip("/")
    known = {item.rstrip("/") for item in settings.effective_cors_origins}
    if settings.web_origin:
        known.add(settings.web_origin.rstrip("/"))
    if origin and origin in known:
        return origin
    if settings.is_hosted:
        return settings.web_origin.rstrip("/") or next(iter(sorted(known)), "")
    return ""


_SIGN_IN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>StudyPilot</title>
<style>
body{{font:16px/1.5 -apple-system,system-ui,sans-serif;background:#0f1117;color:#e6e8ef;
display:grid;place-items:center;min-height:100vh;margin:0}}
main{{max-width:28rem;padding:2rem;text-align:center}}
h1{{font-size:1.25rem;margin:0 0 .5rem}} p{{margin:0;color:#a3a9b8}}
</style></head><body><main><h1>{title}</h1><p>{detail}</p></main></body></html>"""


@router.post("/connectors/{connector_id}/connect", response_model=AssistantConnectorSignInOut)
def connect_connector(
    connector_id: str,
    request: Request,
    context: ContextDep,
    user: UserDep,
    repo: AssistantDep,
) -> AssistantConnectorSignInOut:
    """Begin a sign-in to a connector, and say where to send the browser.

    Finds the connector's authorisation server, makes this application known
    to it if it is not already, and records what the returning browser must
    bring back.
    """
    import secrets as random
    import time

    row = repo.connector(user.id, connector_id)
    settings = context.settings
    origin = _api_origin(context, request)
    redirect_uri = f"{origin}{connector_auth.CALLBACK_PATH}"
    document_url = f"{origin}{connector_auth.CLIENT_DOCUMENT_PATH}"
    try:
        server = connector_auth.discover(row.url)
        record = connector_auth.loads(keys.unseal(settings, row.oauth_ciphertext))
        known = record.get("pending") or record
        if (
            known.get("client_id")
            and known.get("token_endpoint") == server.token_endpoint
            and known.get("registered_for") == redirect_uri
        ):
            # Already registered with this server for this address: use that.
            client = {key: known.get(key, "") for key in ("client_id", "client_secret")}
            client["auth_method"] = known.get("auth_method", "none")
        else:
            client = connector_auth.register(server, redirect_uri, document_url)
        state = random.token_urlsafe(32)
        verifier = connector_auth.new_verifier()
        url = connector_auth.authorization_url(
            server, client, redirect_uri=redirect_uri, state=state, verifier=verifier
        )
        record["pending"] = {
            **client,
            "registered_for": redirect_uri,
            "token_endpoint": server.token_endpoint,
            "resource": server.resource,
            "redirect_uri": redirect_uri,
            "verifier": verifier,
            "return_to": _return_origin(context, request),
            "started_at": time.time(),
        }
        row.oauth_ciphertext = keys.seal(settings, connector_auth.dumps(record))
    except connector_auth.ConnectorAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except keys.KeyStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    row.oauth_state = state
    repo.session.flush()
    return AssistantConnectorSignInOut(authorization_url=url)


@router.post("/connectors/{connector_id}/disconnect", response_model=AssistantExtensionsOut)
def disconnect_connector(
    connector_id: str, user: UserDep, repo: AssistantDep
) -> AssistantExtensionsOut:
    """Sign out of a connector: its tokens and its registration are forgotten here."""
    row = repo.connector(user.id, connector_id)
    row.oauth_ciphertext, row.oauth_state, row.token_ciphertext = "", None, ""
    row.signed_in = False
    repo.session.flush()
    return _extensions(user.id, repo)


@public_router.get(connector_auth.CALLBACK_PATH, include_in_schema=False, response_model=None)
def connector_callback(
    context: ContextDep,
    repo: AssistantDep,
    state: str = "",
    code: str = "",
    error: str = "",
) -> HTMLResponse | RedirectResponse:
    """A connector's sign-in page sends the browser here.

    No credentials of this application's come with it: the state, which only
    the browser that started the sign-in was given, says which connector it is
    for, and it works once.
    """
    import html
    import time
    from urllib.parse import quote

    settings = context.settings
    row = repo.connector_by_state(state)
    outcome, label, detail, return_to = (
        "failed",
        "",
        "This sign-in is not one that was started here.",
        "",
    )
    if row is not None:
        label = row.label
        record = connector_auth.loads(keys.unseal(settings, row.oauth_ciphertext))
        pending = record.pop("pending", None) or {}
        return_to = str(pending.get("return_to") or "")
        row.oauth_state = None  # used, whatever happens next
        fresh = time.time() - float(pending.get("started_at") or 0) < connector_auth.FLOW_SECONDS
        if error or not code:
            outcome, detail = "cancelled", "The sign-in was not completed."
        elif not pending or not fresh:
            detail = "The sign-in took too long. Start it again."
        else:
            try:
                signed_in = connector_auth.exchange(pending, code=code)
                signed_in["registered_for"] = pending.get("registered_for", "")
                row.oauth_ciphertext = keys.seal(settings, connector_auth.dumps(signed_in))
                row.token_ciphertext = keys.seal(settings, signed_in["access_token"])
                row.signed_in = True
                outcome, detail = "connected", f"You are signed in to {label}."
            except (connector_auth.ConnectorAuthError, keys.KeyStorageError) as exc:
                detail = str(exc)
        if outcome != "connected":
            # What was there before the attempt stays: an earlier sign-in still
            # works, and failing that the registration can be used again.
            if not record.get("access_token"):
                names = ("client_id", "client_secret", "auth_method", "registered_for")
                record = {name: pending.get(name, "") for name in (*names, "token_endpoint")}
            row.oauth_ciphertext = (
                keys.seal(settings, connector_auth.dumps(record)) if record.get("client_id") else ""
            )
        repo.session.flush()
    if return_to:
        query = f"signin={outcome}&connector={quote(label)}"
        if outcome == "failed":
            query += f"&reason={quote(detail[:200])}"
        return RedirectResponse(
            f"{return_to}/claude/extensions?{query}", status_code=status.HTTP_303_SEE_OTHER
        )
    title = "Signed in" if outcome == "connected" else "Not signed in"
    return HTMLResponse(
        _SIGN_IN_PAGE.format(
            title=title,
            detail=html.escape(detail) + " You can close this tab and return to StudyPilot.",
        )
    )


@public_router.get(connector_auth.CLIENT_DOCUMENT_PATH, include_in_schema=False)
def connector_client_document(request: Request, context: ContextDep) -> dict[str, object]:
    """This application, described for a connector that asks who is signing in."""
    origin = _api_origin(context, request)
    return connector_auth.client_document(
        f"{origin}{connector_auth.CLIENT_DOCUMENT_PATH}",
        f"{origin}{connector_auth.CALLBACK_PATH}",
    )
