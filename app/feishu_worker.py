import json
import logging

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import SessionLocal
from app.models import ApprovalRequest, RawInput
from app.services.feishu_authorization import (
    authorize_feishu_message,
    identifier_fingerprint,
)
from app.services.approval_service import (
    ApprovalOutcome,
    approval_outcome_reply,
    process_approval_card_action,
    process_approval_message,
)
from app.services.approval_cards import (
    build_card_callback_response,
    parse_approval_card_action,
)
from app.services.feishu_delivery import deliver_with_retry
from app.services.feishu_replies import recorded_reply
from app.services.supervision_notifications import SupervisionNotification
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

        with SessionLocal() as db:
            approval_outcome = process_approval_message(
                db,
                text=text_content,
                tenant_key=tenant_key,
                sender_type=sender.get("sender_type"),
                sender_open_id=sender_open_id,
                chat_type=chat_type,
                chat_id=message.get("chat_id"),
                feishu_event_id=header.get("event_id") or message_id,
                actor_user_id=settings.default_user_id,
                allowed_tenant_keys=settings.allowed_tenant_keys,
                allowed_open_ids=settings.allowed_open_ids,
                allowed_chat_types=settings.allowed_chat_types,
                approver_open_ids=settings.approver_open_ids,
                supervision_chat_id=(
                    settings.feishu_supervision_chat_id.strip()
                ),
            )

        if approval_outcome is not None:
            logger.info(
                "event=feishu_approval_result result=%s message=%s",
                approval_outcome.code,
                identifier_fingerprint(message_id),
            )
            reply_text(message_id, approval_outcome_reply(approval_outcome))
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


def _approval_notification_from_request(
    request: ApprovalRequest,
) -> SupervisionNotification:
    return SupervisionNotification(
        event_type="approval_required",
        repository=request.repository,
        target_sha=request.target_sha,
        pull_request_number=request.pull_request_number,
        target_environment=request.target_environment,
        approval_request_id=request.id,
        action_code=request.action_code,
        expires_at=request.expires_at,
    )


def handle_card_action(
    data: P2CardActionTrigger,
) -> P2CardActionTriggerResponse:
    """Handle a signed Feishu card callback outside message parsing."""

    worker_health.mark_event_received()
    failed = False
    event_id: str | None = None
    operator_open_id: str | None = None
    parsed = None
    outcome = ApprovalOutcome("unavailable")
    notification: SupervisionNotification | None = None
    try:
        payload = json.loads(lark.JSON.marshal(data))
        header = payload.get("header") or {}
        event = payload.get("event") or {}
        operator = event.get("operator") or {}
        context = event.get("context") or {}
        callback_action = event.get("action") or {}
        event_id = header.get("event_id")
        operator_open_id = operator.get("open_id")
        parsed = parse_approval_card_action(callback_action.get("value"))
        if parsed is None:
            outcome = ApprovalOutcome("malformed")
        else:
            with SessionLocal() as db:
                outcome = process_approval_card_action(
                    db,
                    action=parsed.action,
                    request_id=parsed.request_id,
                    tenant_key=header.get("tenant_key")
                    or operator.get("tenant_key"),
                    operator_open_id=operator_open_id,
                    chat_id=context.get("open_chat_id"),
                    feishu_event_id=event_id or "",
                    actor_user_id=settings.default_user_id,
                    allowed_tenant_keys=settings.allowed_tenant_keys,
                    allowed_open_ids=settings.allowed_open_ids,
                    approver_open_ids=settings.approver_open_ids,
                    supervision_chat_id=(
                        settings.feishu_supervision_chat_id.strip()
                    ),
                )
                if outcome.code in {"recorded", "duplicate", "waiting"}:
                    request = db.get(ApprovalRequest, parsed.request_id)
                    if request is not None:
                        notification = _approval_notification_from_request(
                            request
                        )
        logger.info(
            "event=feishu_card_approval_result result=%s action=%s "
            "request=%s actor=%s callback=%s",
            outcome.code,
            parsed.action if parsed is not None else "invalid",
            identifier_fingerprint(
                str(parsed.request_id) if parsed is not None else None
            ),
            identifier_fingerprint(operator_open_id),
            identifier_fingerprint(event_id),
        )
        return P2CardActionTriggerResponse(
            build_card_callback_response(outcome, notification)
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        failed = True
        logger.error(
            "event=feishu_card_approval_failed reason=invalid_callback"
        )
        return P2CardActionTriggerResponse(
            build_card_callback_response(ApprovalOutcome("malformed"), None)
        )
    except Exception as exc:
        failed = True
        logger.error(
            "event=feishu_card_approval_failed reason=unhandled_error "
            "exception_type=%s",
            type(exc).__name__,
        )
        return P2CardActionTriggerResponse(
            build_card_callback_response(
                ApprovalOutcome("unavailable"),
                None,
            )
        )
    finally:
        if failed:
            worker_health.mark_event_failed()
        else:
            worker_health.mark_event_succeeded()


event_handler = (
    lark.EventDispatcherHandler.builder("", "")
    .register_p2_im_message_receive_v1(handle_message)
    .register_p2_card_action_trigger(handle_card_action)
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
