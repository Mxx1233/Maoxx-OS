import json
import logging

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import SessionLocal
from app.models import RawInput
from app.services.feishu_authorization import (
    authorize_feishu_message,
    identifier_fingerprint,
)
from app.services.feishu_replies import recorded_reply


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger("maoxx-feishu-worker")


api_client = (
    lark.Client.builder()
    .app_id(settings.feishu_app_id)
    .app_secret(settings.feishu_app_secret)
    .log_level(lark.LogLevel.INFO)
    .build()
)


def reply_text(message_id: str, text: str) -> None:
    """Reply to one Feishu message."""

    request = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            ReplyMessageRequestBody.builder()
            .msg_type("text")
            .content(
                json.dumps(
                    {"text": text},
                    ensure_ascii=False,
                )
            )
            .build()
        )
        .build()
    )

    response = api_client.im.v1.message.reply(request)

    if not response.success():
        logger.error(
            "Reply failed: code=%s msg=%s log_id=%s",
            response.code,
            response.msg,
            response.get_log_id(),
        )
        return

    logger.info(
        "Reply sent successfully: message_id=%s",
        message_id,
    )


def handle_message(data: P2ImMessageReceiveV1) -> None:
    """Save Feishu text messages to PostgreSQL and reply."""

    try:
        payload = json.loads(
            lark.JSON.marshal(data)
        )

        header = payload.get("header") or {}
        event = payload.get("event") or {}
        sender = event.get("sender") or {}
        sender_id = sender.get("sender_id") or {}
        message = event.get("message") or {}

        tenant_key = header.get("tenant_key")
        sender_open_id = sender_id.get("open_id")
        chat_type = message.get("chat_type")

        authorization = authorize_feishu_message(
            tenant_key=tenant_key,
            sender_type=sender.get("sender_type"),
            sender_open_id=sender_open_id,
            chat_type=chat_type,
            allowed_tenant_keys=settings.allowed_tenant_keys,
            allowed_open_ids=settings.allowed_open_ids,
            allowed_chat_types=settings.allowed_chat_types,
        )

        if not authorization.allowed:
            logger.warning(
                "Rejected Feishu event: reason=%s tenant=%s sender=%s",
                authorization.reason,
                identifier_fingerprint(tenant_key),
                identifier_fingerprint(sender_open_id),
            )
            return

        message_id = message.get("message_id")
        message_type = message.get("message_type")

        if not message_id:
            logger.warning("Message event has no message_id")
            return

        if message_type != "text":
            reply_text(
                message_id,
                "消息已收到。目前第一版只支持文字记录，图片功能稍后接入。",
            )
            return

        content = json.loads(
            message.get("content") or "{}"
        )

        text_content = str(
            content.get("text") or ""
        ).strip()

        if not text_content:
            reply_text(
                message_id,
                "没有识别到有效文字内容。",
            )
            return

        metadata = {
            "event_id": header.get("event_id"),
            "sender_open_id": sender_open_id,
            "sender_union_id": sender_id.get("union_id"),
            "chat_id": message.get("chat_id"),
            "chat_type": chat_type,
            "create_time": message.get("create_time"),
        }

        record = RawInput(
            user_id=settings.default_user_id,
            channel_code="feishu",
            external_message_id=message_id,
            input_type="text",
            text_content=text_content,
            processing_status="pending",
            metadata_json=metadata,
        )

        created = False

        with SessionLocal() as db:
            db.add(record)

            try:
                db.commit()
                db.refresh(record)
                created = True

            except IntegrityError:
                db.rollback()
                logger.info(
                    "Duplicate Feishu message ignored: %s",
                    message_id,
                )

        if created:
            logger.info(
                "Saved Feishu message: record_id=%s message_id=%s",
                record.id,
                message_id,
            )

            reply_text(
                message_id,
                recorded_reply(),
            )
        else:
            reply_text(
                message_id,
                "这条消息已经记录过了。",
            )

    except json.JSONDecodeError:
        logger.exception(
            "Failed to decode Feishu message content"
        )

    except Exception:
        logger.exception(
            "Failed to process Feishu message"
        )


event_handler = (
    lark.EventDispatcherHandler.builder("", "")
    .register_p2_im_message_receive_v1(handle_message)
    .build()
)


def main() -> None:
    logger.info("Starting Maoxx OS Feishu Worker")

    ws_client = lark.ws.Client(
        settings.feishu_app_id,
        settings.feishu_app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )

    ws_client.start()


if __name__ == "__main__":
    main()
