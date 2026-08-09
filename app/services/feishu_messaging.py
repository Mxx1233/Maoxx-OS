import json
import logging
from typing import Protocol

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
)

from app.services.feishu_delivery import DeliveryResult, deliver_with_retry


logger = logging.getLogger("maoxx-feishu-worker")


class FeishuMessageClient(Protocol):
    class _Im(Protocol):
        class _V1(Protocol):
            class _Message(Protocol):
                def create(self, request): ...

            message: _Message

        v1: _V1

    im: _Im


def build_feishu_client(app_id: str, app_secret: str):
    return (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .log_level(lark.LogLevel.ERROR)
        .build()
    )


def _extract_message_id(response: object) -> str | None:
    data = getattr(response, "data", None)
    if data is None:
        return None
    message_id = getattr(data, "message_id", None)
    if isinstance(message_id, str) and message_id:
        return message_id
    return None


def send_structured_text(
    client: FeishuMessageClient,
    *,
    chat_id: str,
    text: str,
    max_attempts: int,
    backoff_seconds: float,
) -> DeliveryResult:
    if not chat_id:
        raise ValueError("supervision chat is not configured")

    request = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        .build()
    )
    result = deliver_with_retry(
        lambda: client.im.v1.message.create(request),
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
        message_id_of=_extract_message_id,
    )
    logger.info(
        "event=feishu_supervision_delivery sent=%s attempts=%s reason=%s",
        result.sent,
        result.attempts,
        result.failure_reason or "none",
    )
    return result


def send_interactive_card(
    client: FeishuMessageClient,
    *,
    chat_id: str,
    card: dict,
    max_attempts: int,
    backoff_seconds: float,
) -> DeliveryResult:
    if not chat_id:
        raise ValueError("supervision chat is not configured")

    request = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("interactive")
            .content(json.dumps(card, ensure_ascii=False))
            .build()
        )
        .build()
    )
    result = deliver_with_retry(
        lambda: client.im.v1.message.create(request),
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
        message_id_of=_extract_message_id,
    )
    logger.info(
        "event=feishu_approval_card_delivery sent=%s attempts=%s reason=%s",
        result.sent,
        result.attempts,
        result.failure_reason or "none",
    )
    return result
