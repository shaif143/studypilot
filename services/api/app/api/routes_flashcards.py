"""Flashcards and spaced-repetition review."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import (
    ContextDep,
    FlashcardsDep,
    SessionDep,
    UserDep,
    UsersDep,
    require_token,
)
from app.api.schemas import FlashcardGenerateRequest, FlashcardOut, FlashcardReviewIn
from app.assessment.service import NoMaterialError, generate_flashcards, review_flashcard

router = APIRouter(
    prefix="/api/flashcards", tags=["flashcards"], dependencies=[Depends(require_token)]
)


@router.post("/generate", response_model=list[FlashcardOut], status_code=status.HTTP_201_CREATED)
def generate(
    payload: FlashcardGenerateRequest,
    context: ContextDep,
    session: SessionDep,
    user: UserDep,
    users: UsersDep,
) -> list[FlashcardOut]:
    try:
        cards, _warnings = generate_flashcards(
            context=context,
            db_session=session,
            user_id=user.id,
            course_id=payload.course_id,
            document_id=payload.document_id,
            topic_slug=payload.topic_slug,
            page_from=payload.page_from,
            page_to=payload.page_to,
            count=payload.count,
            allow_cloud=payload.allow_cloud,
            user_settings=users.settings(user.id),
        )
    except NoMaterialError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return [FlashcardOut.model_validate(card) for card in cards]


@router.get("", response_model=list[FlashcardOut])
def list_cards(
    user: UserDep,
    flashcards: FlashcardsDep,
    course_id: str | None = None,
    document_id: str | None = None,
    due_only: bool = False,
    limit: int = 100,
) -> list[FlashcardOut]:
    return [
        FlashcardOut.model_validate(card)
        for card in flashcards.list(
            user.id,
            course_id=course_id,
            document_id=document_id,
            due_only=due_only,
            limit=min(limit, 500),
        )
    ]


@router.post("/{card_id}/review", response_model=FlashcardOut)
def review(
    card_id: str, payload: FlashcardReviewIn, session: SessionDep, user: UserDep
) -> FlashcardOut:
    """Record a review and reschedule the card (0 again, 1 hard, 2 good, 3 easy)."""
    card = review_flashcard(
        db_session=session, user_id=user.id, card_id=card_id, rating=payload.rating
    )
    return FlashcardOut.model_validate(card)


@router.post("/{card_id}/suspend", response_model=FlashcardOut)
def suspend(
    card_id: str, session: SessionDep, user: UserDep, flashcards: FlashcardsDep
) -> FlashcardOut:
    card = flashcards.get(user.id, card_id)
    card.suspended = True
    session.flush()
    return FlashcardOut.model_validate(card)
