"""Models working an assignment out together, and the folders that organise a course."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import (
    ContextDep,
    CouncilDep,
    CoursesDep,
    DocumentsDep,
    FoldersDep,
    SessionDep,
    UserDep,
    UsersDep,
    require_token,
)
from app.api.schemas import (
    CouncilRunOut,
    CouncilSeatOut,
    CouncilStartRequest,
    CouncilStateOut,
    FolderCreate,
    FolderOut,
    FolderUpdate,
)
from app.council import service as council
from app.storage.models import CouncilRun

router = APIRouter(prefix="/api", tags=["council"], dependencies=[Depends(require_token)])

#: What may be used as a reference: anything readable that is not itself a brief.
READABLE = {"indexed", "partial"}
KINDS = ("guide", "solution")


def _run_out(run: CouncilRun | None) -> CouncilRunOut | None:
    if run is None:
        return None
    out = CouncilRunOut.model_validate(run)
    # A run left "running" by a backend that was quit mid-discussion is not running.
    if out.status in {"queued", "running"} and not council.is_running(run.id):
        out.status = "cancelled"
        out.phase_detail = "The app was closed while this was running. Start it again."
    return out


def _state(
    context: ContextDep,
    user_id: str,
    document_id: str,
    users: UsersDep,
    runs: CouncilDep,
    *,
    allow_cloud: bool,
) -> CouncilStateOut:
    seats = council.describe_seats(
        context.settings, users.settings(user_id), allow_cloud=allow_cloud
    )
    return CouncilStateOut(
        seats=[
            CouncilSeatOut(**{key: seat[key] for key in CouncilSeatOut.model_fields})
            for seat in seats
        ],
        guide=_run_out(runs.latest(user_id, document_id, "guide")),
        solution=_run_out(runs.latest(user_id, document_id, "solution")),
    )


@router.get("/assignments/{document_id}/council", response_model=CouncilStateOut)
def council_state(
    document_id: str,
    context: ContextDep,
    user: UserDep,
    users: UsersDep,
    documents: DocumentsDep,
    runs: CouncilDep,
    allow_cloud: bool = False,
) -> CouncilStateOut:
    """The four seats, and the latest guide and completed work for this assignment.

    Polled while a discussion runs: the run carries everything said so far.
    """
    documents.get(user.id, document_id)  # ownership
    return _state(context, user.id, document_id, users, runs, allow_cloud=allow_cloud)


@router.post(
    "/assignments/{document_id}/council/{kind}",
    response_model=CouncilStateOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_council(
    document_id: str,
    kind: str,
    payload: CouncilStartRequest,
    context: ContextDep,
    user: UserDep,
    users: UsersDep,
    documents: DocumentsDep,
    runs: CouncilDep,
    session: SessionDep,
) -> CouncilStateOut:
    """Start a discussion: the step-by-step guide, or the completed assignment.

    Returns at once; the discussion runs in the background and is read from
    this assignment's ``/council``. Completing the assignment needs a guide
    the models have already agreed on, because it is written by following it.
    """
    if kind not in KINDS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
    brief = documents.get(user.id, document_id)
    if brief.kind != "assignment":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This works on assignment briefs. Mark this document as one first.",
        )
    if brief.status not in READABLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This brief is not readable yet (status: {brief.status}).",
        )
    user_settings = users.settings(user.id)
    if user_settings.assignment_policy == "off":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assignment help is switched off in Settings.",
        )
    current = runs.latest(user.id, document_id, kind)
    if current is not None and council.is_running(current.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This is already being worked on. Stop it first to start again.",
        )
    if kind == "solution" and runs.latest_done(user.id, document_id, "guide") is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Have the step-by-step guide worked out first; the assignment is completed "
            "by following it.",
        )

    seats = council.describe_seats(context.settings, user_settings, allow_cloud=payload.allow_cloud)
    ready = council.members_from(seats, list(payload.members))
    if len(ready) < 2:
        not_ready = "; ".join(
            f"{seat['name']}: {seat['detail']}" for seat in seats if not seat["ready"]
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A discussion needs at least two models, and "
                f"{'only ' + ready[0].name + ' is' if ready else 'none is'} ready. {not_ready}"
            ),
        )

    # The references: what was chosen, limited to what is in this course and readable.
    in_course = {
        doc.id: doc
        for doc in documents.list(user.id, brief.course_id)
        if doc.status in READABLE and doc.id != brief.id
    }
    if payload.reference_document_ids:
        references = [doc_id for doc_id in payload.reference_document_ids if doc_id in in_course]
    else:
        references = [
            doc_id for doc_id, doc in in_course.items() if doc.kind in {"lecture", "textbook"}
        ]

    run = runs.create(
        user_id=user.id,
        course_id=brief.course_id,
        document_id=brief.id,
        kind=kind,
        status="queued",
        phase="queued",
        phase_detail="Starting.",
        reference_document_ids=references,
    )
    session.commit()  # the job's own session must see the row
    council.start(
        context,
        run_id=run.id,
        user_id=user.id,
        allow_cloud=payload.allow_cloud,
        member_keys=list(payload.members) or None,
    )
    return _state(context, user.id, document_id, users, runs, allow_cloud=payload.allow_cloud)


@router.delete("/assignments/{document_id}/council/{kind}", response_model=CouncilStateOut)
def stop_council(
    document_id: str,
    kind: str,
    context: ContextDep,
    user: UserDep,
    users: UsersDep,
    documents: DocumentsDep,
    runs: CouncilDep,
) -> CouncilStateOut:
    """Stop a discussion. What has been said so far is kept."""
    documents.get(user.id, document_id)
    current = runs.latest(user.id, document_id, kind) if kind in KINDS else None
    if current is not None:
        council.stop(current.id)
    return _state(context, user.id, document_id, users, runs, allow_cloud=False)


# --- folders -----------------------------------------------------------------------------
def _folders_out(user_id: str, course_id: str, folders: FoldersDep) -> list[FolderOut]:
    counts = folders.counts(user_id, course_id)
    out = []
    for folder in folders.list(user_id, course_id):
        item = FolderOut.model_validate(folder)
        item.document_count = counts.get(folder.id, 0)
        out.append(item)
    return out


@router.get("/courses/{course_id}/folders", response_model=list[FolderOut])
def list_folders(
    course_id: str, user: UserDep, courses: CoursesDep, folders: FoldersDep
) -> list[FolderOut]:
    courses.get(user.id, course_id)  # ownership
    return _folders_out(user.id, course_id, folders)


@router.post(
    "/courses/{course_id}/folders",
    response_model=list[FolderOut],
    status_code=status.HTTP_201_CREATED,
)
def create_folder(
    course_id: str, payload: FolderCreate, user: UserDep, courses: CoursesDep, folders: FoldersDep
) -> list[FolderOut]:
    """Make a folder in a course, among its lectures or among its assignments."""
    courses.get(user.id, course_id)
    name = " ".join(payload.name.split())
    if any(
        folder.name.lower() == name.lower() and folder.section == payload.section
        for folder in folders.list(user.id, course_id)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="There is already a folder with that name."
        )
    folders.create(user.id, course_id, name=name, section=payload.section)
    return _folders_out(user.id, course_id, folders)


@router.patch("/folders/{folder_id}", response_model=list[FolderOut])
def rename_folder(
    folder_id: str, payload: FolderUpdate, user: UserDep, folders: FoldersDep
) -> list[FolderOut]:
    folder = folders.get(user.id, folder_id)
    folder.name = " ".join(payload.name.split())
    folders.session.flush()
    return _folders_out(user.id, folder.course_id, folders)


@router.delete("/folders/{folder_id}", response_model=list[FolderOut])
def delete_folder(folder_id: str, user: UserDep, folders: FoldersDep) -> list[FolderOut]:
    """Remove a folder. The files in it stay in the course, unfiled."""
    folder = folders.get(user.id, folder_id)
    course_id = folder.course_id
    folders.delete(user.id, folder_id)
    return _folders_out(user.id, course_id, folders)
