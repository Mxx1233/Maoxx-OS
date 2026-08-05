from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import RawInput
from app.schemas.raw_input import RawInputResponse, TextInputCreate


router = APIRouter(
    prefix="/inputs",
    tags=["inputs"],
)


@router.post(
    "/text",
    response_model=RawInputResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_text_input(
    payload: TextInputCreate,
    db: Session = Depends(get_db),
) -> RawInput:
    """Store one original text input."""

    record = RawInput(
        user_id=settings.default_user_id,
        channel_code=payload.channel_code,
        external_message_id=payload.external_message_id,
        input_type="text",
        text_content=payload.text.strip(),
        processing_status="pending",
        metadata_json={},
    )

    db.add(record)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The external message has already been recorded.",
        ) from exc

    db.refresh(record)
    return record


@router.get(
    "/recent",
    response_model=list[RawInputResponse],
)
def get_recent_inputs(
    limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[RawInput]:
    """Return the current user's most recent inputs."""

    statement = (
        select(RawInput)
        .where(RawInput.user_id == settings.default_user_id)
        .order_by(RawInput.received_at.desc())
        .limit(limit)
    )

    return list(db.scalars(statement))
