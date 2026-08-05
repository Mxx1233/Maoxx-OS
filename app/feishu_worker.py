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
from app.services.feishu_delivery import deliver_with_retry
from app.services.feishu_replies import recorded_reply
from app.services.worker_health import (
    WORKER_HEALTH_HOST,
    WORKER_HEALTH_PORT,
    WorkerHealth,
    start_health_server,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger("maoxx-feishu-worker")
worker_health = WorkerHealth()


api_client = (
    lark.Client.builder()
    .app_id(settings.feishu_app_id)
    .app_secret(settings.feishu_app_secret)
    .log_level(lark.LogLevel.ERROR)
    .build()
)


def _send_reply_once(message_id: str, text: str):
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

    return api_client.im.v1.message.reply(request)


def reply_text(message_id: str, text: str) -> bool:
    """Reply with bounded retries and no sensitive identifier logging."""

    result = deliver_with_retry(
        lambda: _send_reply_once(message_id, text),
        max_attempts=settings.feishu_reply_max_attempts,
        backoff_seconds=settings.feishu_reply_backoff_seconds,
    )
    logger.info(
        "event=feishu_reply_result sent=%s attempts=%s message=%s",
        result.sent,
        result.attempts,
        identifier_fingerprint(message_id),
    )
    return result.sent


def handle_message(data: P2ImMessageReceiveV1) -> None:
    """Save Feishu text messages to PostgreSQL and reply."""

    worker_health.mark_event_received()
    failed = False
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
                    "event=feishu_message_duplicate message=%s",
                    identifier_fingerprint(message_id),
                )

        if created:
            logger.info(
                "event=feishu_message_saved record=%s message=%s",
                identifier_fingerprint(str(record.id)),
                identifier_fingerprint(message_id),
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
        failed = True
        logger.error(
            "event=feishu_message_failed reason=invalid_json"
        )

    except Exception as exc:
        failed = True
        logger.error(
            "event=feishu_message_failed reason=unhandled_error "
            "exception_type=%s",
            type(exc).__name__,
        )

    finally:
        if failed:
            worker_health.mark_event_failed()
        else:
            worker_health.mark_event_succeeded()


event_handler = (
    lark.EventDispatcherHandler.builder("", "")
    .register_p2_im_message_receive_v1(handle_message)
    .build()
)


def main() -> None:
    logger.info("event=worker_starting")
    start_health_server(
        worker_health,
        WORKER_HEALTH_HOST,
        WORKER_HEALTH_PORT,
    )

    class ObservableWsClient(lark.ws.Client):
        async def _connect(self) -> None:
            await super()._connect()
            if self._conn is not None:
                worker_health.mark_connected()
                logger.info("event=worker_connected")

        async def _disconnect(self) -> None:
            worker_health.mark_reconnecting()
            logger.warning("event=worker_disconnected")
            await super()._disconnect()

    ws_client = ObservableWsClient(
        settings.feishu_app_id,
        settings.feishu_app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.ERROR,
    )
    ws_client.on_reconnecting = worker_health.mark_reconnecting
    ws_client.on_reconnected = worker_health.mark_connected

    ws_client.start()


if __name__ == "__main__":
    main()
