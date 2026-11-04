"""Course management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import CoursesDep, UserDep, require_token
from app.api.schemas import CourseCreate, CourseOut, CourseUpdate

router = APIRouter(prefix="/api/courses", tags=["courses"], dependencies=[Depends(require_token)])


def _to_out(course, counts: dict[str, int]) -> CourseOut:
    return CourseOut(
        id=course.id,
        title=course.title,
        code=course.code,
        description=course.description,
        institution=course.institution,
        integrity_policy=course.integrity_policy,
        archived=course.archived,
        created_at=course.created_at,
        document_count=counts.get("documents", 0),
        page_count=counts.get("pages", 0),
        topic_count=counts.get("topics", 0),
    )


@router.get("", response_model=list[CourseOut])
def list_courses(
    user: UserDep, courses: CoursesDep, include_archived: bool = False
) -> list[CourseOut]:
    return [
        _to_out(course, courses.counts(user.id, course.id))
        for course in courses.list(user.id, include_archived=include_archived)
    ]


@router.post("", response_model=CourseOut, status_code=status.HTTP_201_CREATED)
def create_course(payload: CourseCreate, user: UserDep, courses: CoursesDep) -> CourseOut:
    course = courses.create(user.id, **payload.model_dump())
    return _to_out(course, {"documents": 0, "pages": 0, "topics": 0})


@router.get("/{course_id}", response_model=CourseOut)
def get_course(course_id: str, user: UserDep, courses: CoursesDep) -> CourseOut:
    course = courses.get(user.id, course_id)
    return _to_out(course, courses.counts(user.id, course_id))


@router.patch("/{course_id}", response_model=CourseOut)
def update_course(
    course_id: str, payload: CourseUpdate, user: UserDep, courses: CoursesDep
) -> CourseOut:
    course = courses.update(user.id, course_id, payload.model_dump(exclude_unset=True))
    return _to_out(course, courses.counts(user.id, course_id))


@router.delete("/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_course(course_id: str, user: UserDep, courses: CoursesDep) -> None:
    """Delete a course and everything in it. Not reversible."""
    courses.delete(user.id, course_id)
