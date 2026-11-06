"""Personalised notes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import (
    ContextDep,
    DriveDep,
    LocalFoldersDep,
    NotesDep,
    SessionDep,
    UserDep,
    UsersDep,
    require_token,
)
from app.api.schemas import NoteGenerateRequest, NoteOut, NoteSavedOut, NoteSaveRequest, NoteUpdate
from app.learning.notes import NoMaterialError, generate_note, persist_note

router = APIRouter(prefix="/api/notes", tags=["notes"], dependencies=[Depends(require_token)])


@router.post("/generate", response_model=NoteOut, status_code=status.HTTP_201_CREATED)
def generate(
    payload: NoteGenerateRequest,
    context: ContextDep,
    session: SessionDep,
    user: UserDep,
    users: UsersDep,
) -> NoteOut:
    try:
        draft = generate_note(
            context=context,
            db_session=session,
            user_id=user.id,
            course_id=payload.course_id,
            document_id=payload.document_id,
            mode=payload.mode,
            topic_slug=payload.topic_slug,
            title=payload.title,
            allow_cloud=payload.allow_cloud,
            user_settings=users.settings(user.id),
        )
    except NoMaterialError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    note = persist_note(
        db_session=session,
        user_id=user.id,
        course_id=payload.course_id,
        document_id=payload.document_id,
        mode=payload.mode,
        draft=draft,
    )
    return NoteOut.model_validate(note)


@router.get("", response_model=list[NoteOut])
def list_notes(user: UserDep, notes: NotesDep, course_id: str | None = None) -> list[NoteOut]:
    return [NoteOut.model_validate(note) for note in notes.list(user.id, course_id)]


@router.get("/{note_id}", response_model=NoteOut)
def get_note(note_id: str, user: UserDep, notes: NotesDep) -> NoteOut:
    return NoteOut.model_validate(notes.get(user.id, note_id))


@router.patch("/{note_id}", response_model=NoteOut)
def update_note(note_id: str, payload: NoteUpdate, user: UserDep, notes: NotesDep) -> NoteOut:
    return NoteOut.model_validate(
        notes.update(user.id, note_id, payload.model_dump(exclude_unset=True))
    )


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(note_id: str, user: UserDep, notes: NotesDep) -> None:
    notes.delete(user.id, note_id)


@router.post("/{note_id}/save", response_model=NoteSavedOut)
def save_note(
    note_id: str,
    payload: NoteSaveRequest,
    context: ContextDep,
    user: UserDep,
    notes: NotesDep,
    folders: LocalFoldersDep,
    drive: DriveDep,
) -> NoteSavedOut:
    """Write the note as Markdown next to the material it came from.

    Into "StudyPilot Notes" under a folder linked on this Mac, or the same
    subfolder of a linked Drive folder. Nothing else in either folder is
    touched, and the file is overwritten on a second save.
    """
    note = notes.get(user.id, note_id)
    if payload.target == "local":
        from app.ingestion.local_folders import FolderError, write_note_to_folder

        link = folders.link(user.id, payload.link_id)
        try:
            path = write_note_to_folder(link, note.title, note.body_markdown)
        except FolderError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return NoteSavedOut(target="local", location=str(path))

    from app.integrations import drive_import
    from app.integrations import google_drive as gd

    link = drive.link(user.id, payload.link_id)
    try:
        location = drive_import.save_note_to_drive(
            context,
            drive,
            user.id,
            folder_id=link.folder_id,
            title=note.title,
            markdown=note.body_markdown,
        )
    except gd.DriveError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return NoteSavedOut(target="drive", location=location)
