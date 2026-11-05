"""Assignment guides: list a course's briefs, draft a plan, approve steps.

A plan is drafted synchronously. Two model calls against a short brief take
ten to forty seconds on a cloud provider, which is tolerable for an action the
student takes once per assignment and sees progress on; a job queue would be
more machinery than the first release needs.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.agents.service import build_retriever, resolve_provider
from app.api.deps import (
    ContextDep,
    CoursesDep,
    DocumentsDep,
    PlansDep,
    SessionDep,
    UserDep,
    UsersDep,
    require_token,
)
from app.api.schemas import (
    AssignmentOut,
    AssignmentPlanOut,
    DocumentOut,
    PlanRequest,
    StepApprovalRequest,
)
from app.assignments.planner import build_plan
from app.storage.models import Document

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["assignments"], dependencies=[Depends(require_token)])

#: Document kinds that count as "what the course taught". Past papers and
#: other briefs are excluded on purpose: a plan grounded in another
#: assignment is not grounded in the slides.
LECTURE_KINDS = {"lecture", "textbook"}


def _brief_text(documents: DocumentsDep, user_id: str, document: Document) -> str:
    pages = documents.pages(user_id, document.id)
    return "\n\n".join(page.text for page in pages if page.text)


@router.get("/courses/{course_id}/assignments", response_model=list[AssignmentOut])
def list_assignments(
    course_id: str,
    user: UserDep,
    courses: CoursesDep,
    documents: DocumentsDep,
    plans: PlansDep,
) -> list[AssignmentOut]:
    courses.get(user.id, course_id)
    latest = plans.latest_by_document(user.id, course_id)
    briefs = [d for d in documents.list(user.id, course_id) if d.kind == "assignment"]
    return [
        AssignmentOut(
            document=DocumentOut.model_validate(brief),
            plan=AssignmentPlanOut.model_validate(latest[brief.id]) if brief.id in latest else None,
        )
        for brief in briefs
    ]


@router.get("/assignments/{document_id}/plan", response_model=AssignmentPlanOut)
def get_plan(document_id: str, user: UserDep, documents: DocumentsDep, plans: PlansDep):
    documents.get(user.id, document_id)
    plan = plans.latest_for_document(user.id, document_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No guide has been drafted yet."
        )
    return AssignmentPlanOut.model_validate(plan)


@router.post(
    "/assignments/{document_id}/plan",
    response_model=AssignmentPlanOut,
    status_code=status.HTTP_201_CREATED,
)
def draft_plan(
    document_id: str,
    body: PlanRequest,
    context: ContextDep,
    db_session: SessionDep,
    user: UserDep,
    courses: CoursesDep,
    documents: DocumentsDep,
    plans: PlansDep,
    users: UsersDep,
) -> AssignmentPlanOut:
    brief = documents.get(user.id, document_id)
    if brief.kind != "assignment":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Guides are drafted from assignment briefs. Mark this document as one first.",
        )
    if brief.status not in {"indexed", "partial"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This brief is not readable yet (status: {brief.status}). {brief.status_detail}"
            ).strip(),
        )
    course = courses.get(user.id, brief.course_id)
    user_settings = users.settings(user.id)

    settings = context.settings
    requested = (
        body.provider
        or settings.assignment_provider
        or user_settings.preferred_llm_provider
        or None
    )
    if requested is None and settings.llm_provider == "extractive":
        # The user asked for the strongest model for assignments; when an
        # Anthropic key is present and nothing is pinned, prefer it.
        from app.models.llm import AnthropicProvider

        if AnthropicProvider().info.available:
            requested = "anthropic"
    provider, provider_warnings = resolve_provider(
        settings, user_settings, requested=requested, allow_cloud_request=body.allow_cloud
    )

    lecture_ids = [
        d.id
        for d in documents.list(user.id, brief.course_id)
        if d.kind in LECTURE_KINDS and d.status in {"indexed", "partial"}
    ]

    # --- memory: what the student decided before -------------------------
    # On the previous draft of this brief, and anywhere in this course.
    previous = plans.latest_for_document(user.id, brief.id)
    prior_decisions = [
        {
            "title": step.get("title", ""),
            "approval": step.get("approval"),
            "missing_terms": step.get("missing_terms") or [],
        }
        for step in (previous.steps if previous else [])
        if step.get("approval") in {"approved", "declined"}
    ]
    accepted_gaps: list[str] = []
    for plan_row in plans.latest_by_document(user.id, brief.course_id).values():
        for step in plan_row.steps:
            if step.get("approval") == "approved" and step.get("grounding") == "beyond":
                for term in step.get("missing_terms") or []:
                    if term not in accepted_gaps:
                        accepted_gaps.append(term)

    draft = build_plan(
        settings=settings,
        llm=provider,
        retriever=build_retriever(context, db_session),
        user_id=user.id,
        course_id=brief.course_id,
        brief_title=brief.title,
        brief_text=_brief_text(documents, user.id, brief),
        lecture_document_ids=lecture_ids,
        integrity_policy=course.integrity_policy,
        # End the read transaction before each model call; see build_plan.
        release_db=db_session.commit,
        prior_decisions=prior_decisions,
        accepted_gaps=accepted_gaps,
    )
    record = draft.to_record()
    record["warnings"] = provider_warnings + record["warnings"]

    plan = plans.create(
        user.id,
        course_id=brief.course_id,
        document_id=brief.id,
        **record,
    )
    log.info(
        "assignment_plan_drafted",
        extra={
            "document_id": brief.id,
            "provider": plan.provider,
            "generative": plan.generative,
            "steps": len(plan.steps),
            "pending": sum(1 for s in plan.steps if s.get("approval") == "pending"),
            "status": plan.status,
        },
    )
    return AssignmentPlanOut.model_validate(plan)


@router.post(
    "/assignments/plans/{plan_id}/steps/{step_index}/approval",
    response_model=AssignmentPlanOut,
)
def set_step_approval(
    plan_id: str,
    step_index: int,
    body: StepApprovalRequest,
    user: UserDep,
    plans: PlansDep,
) -> AssignmentPlanOut:
    try:
        plan = plans.set_step_approval(user.id, plan_id, step_index, body.approval)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return AssignmentPlanOut.model_validate(plan)
