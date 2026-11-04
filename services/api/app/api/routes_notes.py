"""Personalised notes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import ContextDep, NotesDep, SessionDep, UserDep, UsersDep, require_token
from app.api.schemas import NoteGenerateRequest, NoteOut, NoteUpdate
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
