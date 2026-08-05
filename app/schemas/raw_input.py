from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TextInputCreate(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=20000,
    )
    channel_code: str = Field(
        default="api_test",
        min_length=1,
        max_length=32,
    )
    external_message_id: str | None = Field(
        default=None,
        max_length=200,
    )


class RawInputResponse(BaseModel):
    id: UUID
    user_id: UUID
    channel_code: str
    external_message_id: str | None
    input_type: str
    text_content: str | None
    processing_status: str
    received_at: datetime
    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )
