from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.services.approval_service import ApprovalOutcome
from app.services.supervision_notifications import SupervisionNotification


CARD_ACTIONS = frozenset({"approve", "reject", "wait"})


@dataclass(frozen=True)
class ApprovalCardAction:
    action: str
    request_id: UUID


def parse_approval_card_action(
    value: object,
) -> ApprovalCardAction | None:
    if not isinstance(value, dict) or set(value) != {
        "action",
        "request_id",
    }:
        return None
    action = value.get("action")
    request_text = value.get("request_id")
    if action not in CARD_ACTIONS or not isinstance(request_text, str):
        return None
    try:
        request_id = UUID(request_text)
    except ValueError:
        return None
    if str(request_id) != request_text:
        return None
    return ApprovalCardAction(action, request_id)


def _approval_details(notification: SupervisionNotification) -> str:
    assert notification.approval_request_id is not None
    assert notification.action_code is not None
    assert notification.expires_at is not None
    action_summary = {
        "development_plan": "开发计划确认",
        "merge_pr": "合并进入 Staging",
        "production_deploy": "Production 部署授权",
    }[notification.action_code]
    lines = [
        f"**动作：** {action_summary}",
        f"**仓库：** {notification.repository}",
    ]
    if notification.pull_request_number is not None:
        lines.append(f"**PR：** #{notification.pull_request_number}")
    lines.extend(
        [
            f"**目标 SHA：** {notification.target_sha}",
            f"**环境：** {notification.target_environment}",
            f"**过期时间：** {notification.expires_at.isoformat()}",
            f"**请求：** {notification.approval_request_id}",
        ]
    )
    return "\n".join(lines)


def build_approval_card(
    notification: SupervisionNotification,
    *,
    state: str = "pending",
) -> dict[str, Any]:
    notification.validate()
    if notification.event_type != "approval_required":
        raise ValueError("approval card requires approval notification")
    if state not in {"pending", "waiting", "approved", "rejected"}:
        raise ValueError("invalid approval card state")

    state_text = {
        "pending": "状态：等待审批",
        "waiting": "状态：稍后处理 / 仍待审批",
        "approved": "状态：已批准（仅记录决定，未执行受保护动作）",
        "rejected": "状态：已拒绝（未执行受保护动作）",
    }[state]
    template = {
        "pending": "orange",
        "waiting": "yellow",
        "approved": "green",
        "rejected": "red",
    }[state]
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": _approval_details(notification),
            },
        },
        {
            "tag": "div",
            "text": {"tag": "plain_text", "content": state_text},
        },
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "卡片只记录人工决定，不会执行合并或部署。",
                }
            ],
        },
    ]
    if state in {"pending", "waiting"}:
        assert notification.approval_request_id is not None
        request_id = str(notification.approval_request_id)
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "批准"},
                        "type": "primary",
                        "value": {
                            "action": "approve",
                            "request_id": request_id,
                        },
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "拒绝"},
                        "type": "danger",
                        "value": {
                            "action": "reject",
                            "request_id": request_id,
                        },
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "等一下"},
                        "value": {
                            "action": "wait",
                            "request_id": request_id,
                        },
                    },
                ],
            }
        )
    return {
        "config": {
            "wide_screen_mode": True,
            "enable_forward": False,
            "update_multi": False,
        },
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": "Maoxx OS 人工审批"},
        },
        "elements": elements,
    }


def build_card_callback_response(
    outcome: ApprovalOutcome,
    notification: SupervisionNotification | None,
) -> dict[str, Any]:
    toast_type, content = {
        "recorded": (
            "success",
            "审批决定已记录。未执行任何受保护动作。",
        ),
        "duplicate": ("info", "该卡片操作已处理。"),
        "waiting": ("info", "已标记为稍后处理，请在过期前决定。"),
        "malformed": ("error", "卡片操作无效。"),
        "unauthorized": ("error", "无权执行该审批。"),
        "invalid_event": ("error", "回调事件无效。"),
        "unknown_request": ("error", "审批请求不存在或不可用。"),
        "expired": ("warning", "审批请求已过期。"),
        "already_decided": ("warning", "审批请求已经处理。"),
        "event_conflict": ("error", "审批事件冲突。"),
        "conflict": ("error", "审批请求发生冲突。"),
        "unavailable": ("error", "审批服务暂时不可用。"),
    }[outcome.code]
    response: dict[str, Any] = {
        "toast": {"type": toast_type, "content": content}
    }
    if notification is not None and outcome.code in {
        "recorded",
        "duplicate",
        "waiting",
    }:
        state = "waiting"
        if outcome.decision_code == "approved":
            state = "approved"
        elif outcome.decision_code == "rejected":
            state = "rejected"
        response["card"] = {
            "type": "raw",
            "data": build_approval_card(notification, state=state),
        }
    return response
