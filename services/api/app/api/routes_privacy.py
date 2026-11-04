"""Data export, deletion and the experimental attention module.

Attention tracking is intentionally inert in this release: the API accepts
*simulated* dwell aggregates so the interface and the UI can be built and
tested, and refuses ``webcam`` samples unless the user has explicitly enabled
the feature.  No camera code ships yet, and raw gaze coordinates are never
accepted by this endpoint at all — only per-element dwell totals.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import (
    AssessmentsDep,
    AttentionDep,
    ContextDep,
    CoursesDep,
    DocumentsDep,
    FlashcardsDep,
    MasteryDep,
    NotesDep,
    SessionDep,
    SessionsDep,
    UserDep,
    UsersDep,
    require_token,
)
from app.api.schemas import (
    AttentionBatchIn,
    AttentionReportOut,
    DeleteResultOut,
    ExportResponse,
)
from app.storage.models import AttentionSample

router = APIRouter(prefix="/api", tags=["privacy"], dependencies=[Depends(require_token)])


@router.get("/privacy/export", response_model=ExportResponse)
def export_data(
    session: SessionDep,
    user: UserDep,
    users: UsersDep,
    courses: CoursesDep,
    documents: DocumentsDep,
    sessions: SessionsDep,
    flashcards: FlashcardsDep,
    assessments: AssessmentsDep,
    notes: NotesDep,
    mastery: MasteryDep,
) -> ExportResponse:
    """Everything stored about this profile, as JSON.

    Original uploaded files are not inlined — they are already on disk under
    the data directory, and the path is reported instead of base64 bulk.
    """
    settings_row = users.settings(user.id)
    return ExportResponse(
        exported_at=datetime.now(UTC),
        profile={
            "id": user.id,
            "handle": user.handle,
            "display_name": user.display_name,
            "created_at": user.created_at.isoformat(),
            "settings": {
                column.name: getattr(settings_row, column.name)
                for column in settings_row.__table__.columns
            },
        },
        courses=[
            {column.name: getattr(course, column.name) for column in course.__table__.columns}
            for course in courses.list(user.id, include_archived=True)
        ],
        documents=[
            {column.name: getattr(document, column.name) for column in document.__table__.columns}
            for document in documents.list(user.id)
        ],
        sessions=[
            {
                "id": row.id,
                "course_id": row.course_id,
                "document_id": row.document_id,
                "current_page": row.current_page,
                "last_active_at": row.last_active_at.isoformat(),
                "messages": [
                    {
                        "role": message.role,
                        "content": message.content,
                        "created_at": message.created_at.isoformat(),
                        "meta": message.meta,
                    }
                    for message in sessions.messages(user.id, row.id)
                ],
            }
            for row in sessions.list(user.id, limit=500)
        ],
        flashcards=[
            {column.name: getattr(card, column.name) for column in card.__table__.columns}
            for card in flashcards.list(user.id, limit=5000)
        ],
        assessments=[
            {
                "id": assessment.id,
                "kind": assessment.kind,
                "title": assessment.title,
                "total_marks": assessment.total_marks,
                "generator": assessment.generator,
                "created_at": assessment.created_at.isoformat(),
                "questions": [
                    {
                        "prompt": question.prompt,
                        "kind": question.kind,
                        "provenance": question.provenance,
                        "marks": question.marks,
                        "topic_slug": question.topic_slug,
                    }
                    for question in assessments.questions(assessment.id)
                ],
            }
            for assessment in assessments.list(user.id, limit=500)
        ],
        notes=[
            {column.name: getattr(note, column.name) for column in note.__table__.columns}
            for note in notes.list(user.id)
        ],
        mastery=[
            {column.name: getattr(estimate, column.name) for column in estimate.__table__.columns}
            for estimate in mastery.list(user.id)
        ],
        note=(
            "Uploaded files are stored on this device under the data directory and are "
            "not included inline in this export."
        ),
    )


@router.delete("/privacy/data", response_model=DeleteResultOut)
def delete_all_data(
    context: ContextDep,
    session: SessionDep,
    user: UserDep,
    users: UsersDep,
) -> DeleteResultOut:
    """Erase this profile and every derived index. Not reversible."""
    user_id = user.id
    context.sparse_index.delete_for_user(session, user_id)
    # The embedded vector store is per-device and single-profile; resetting it
    # is the only way to be certain no vectors survive.
    context.vector_store.reset()
    context.vector_store.ensure_collection(context.embedder.dimensions)
    context.blobs.clear()
    users.delete_all_data(user_id)
    return DeleteResultOut(
        deleted=True,
        detail=(
            "Profile, courses, documents, indexes, uploaded files and progress have "
            "been deleted from this device."
        ),
    )


@router.post("/attention/samples", response_model=AttentionReportOut)
def record_attention(
    payload: AttentionBatchIn,
    user: UserDep,
    users: UsersDep,
    documents: DocumentsDep,
    attention: AttentionDep,
) -> AttentionReportOut:
    """Accept per-element dwell aggregates. Never raw gaze or camera data."""
    settings_row = users.settings(user.id)
    documents.get(user.id, payload.document_id)  # ownership check

    if any(sample.source == "webcam" for sample in payload.samples):
        if not settings_row.allow_attention_tracking:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Camera-based attention estimation is switched off. Enable it in "
                    "Settings first. Note that camera capture is not implemented in "
                    "this release."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "Camera-based gaze estimation is not implemented in this release. "
                "Only simulated attention data is accepted."
            ),
        )

    attention.add_samples(
        [
            AttentionSample(
                user_id=user.id,
                document_id=payload.document_id,
                page_number=sample.page_number,
                element_index=sample.element_index,
                dwell_ms=sample.dwell_ms,
                source=sample.source,
            )
            for sample in payload.samples
        ]
    )
    return _attention_report(user.id, payload.document_id, attention, settings_row)


@router.get("/attention/{document_id}", response_model=AttentionReportOut)
def attention_report(
    document_id: str,
    user: UserDep,
    users: UsersDep,
    documents: DocumentsDep,
    attention: AttentionDep,
) -> AttentionReportOut:
    documents.get(user.id, document_id)
    return _attention_report(user.id, document_id, attention, users.settings(user.id))


@router.delete("/attention/{document_id}", response_model=DeleteResultOut)
def delete_attention(user: UserDep, attention: AttentionDep) -> DeleteResultOut:
    removed = attention.delete_all(user.id)
    return DeleteResultOut(deleted=True, detail=f"Deleted {removed} attention records.")


def _attention_report(
    user_id: str, document_id: str, attention, settings_row
) -> AttentionReportOut:
    totals = attention.aggregate_for_document(user_id, document_id)
    by_page: dict[int, list[dict]] = {}
    for row in totals:
        by_page.setdefault(int(row["page_number"]), []).append(row)

    low: list[dict] = []
    for page_number, rows in by_page.items():
        if len(rows) < 3:
            continue
        average = sum(int(row["dwell_ms"]) for row in rows) / len(rows)
        for row in rows:
            if int(row["dwell_ms"]) < average * 0.4:
                low.append(
                    {
                        "page_number": page_number,
                        "element_index": row["element_index"],
                        "dwell_ms": row["dwell_ms"],
                        "page_average_ms": round(average),
                    }
                )

    return AttentionReportOut(
        document_id=document_id,
        low_attention=low,
        totals=totals,
        enabled=bool(settings_row.allow_attention_tracking),
        note=(
            "Dwell time is not comprehension. These are areas you spent comparatively "
            "little time on, which may be worth a second look — nothing more. "
            "Camera-based tracking is not implemented in this release; only simulated "
            "data is recorded."
        ),
    )
