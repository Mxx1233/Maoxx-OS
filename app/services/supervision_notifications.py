from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.services.approval_service import (
    APPROVAL_ACTIONS,
    REPOSITORY_PATTERN,
    SHA_PATTERN,
)


NOTIFICATION_EVENTS = frozenset(
    {
        "pr_created",
        "ci_passed",
        "ci_failed",
        "staging_started",
        "staging_passed",
        "staging_failed",
        "approval_required",
    }
)


class NotificationValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SupervisionNotification:
    event_type: str
    repository: str
    target_sha: str
    pull_request_number: int | None = None
    target_environment: str | None = None
    approval_request_id: UUID | None = None
    action_code: str | None = None
    expires_at: datetime | None = None

    def validate(self) -> None:
        if self.event_type not in NOTIFICATION_EVENTS:
            raise NotificationValidationError("unknown event_type")
        if not REPOSITORY_PATTERN.fullmatch(
            self.repository
        ) or self.repository.endswith(".git"):
            raise NotificationValidationError("invalid repository")
        if not SHA_PATTERN.fullmatch(self.target_sha):
            raise NotificationValidationError("invalid target_sha")
        if (
            self.pull_request_number is not None
            and self.pull_request_number < 1
        ):
            raise NotificationValidationError("invalid pull_request_number")
        if (
            self.target_environment is not None
            and self.target_environment
            not in {
                "development",
                "staging",
                "production",
            }
        ):
            raise NotificationValidationError("invalid target_environment")

        if self.event_type in {"pr_created", "ci_passed", "ci_failed"}:
            if self.pull_request_number is None:
                raise NotificationValidationError("event requires PR number")
            if self.target_environment is not None:
                raise NotificationValidationError(
                    "PR/CI event forbids environment"
                )
        if self.event_type.startswith("staging_"):
            if self.target_environment != "staging":
                raise NotificationValidationError(
                    "staging event requires staging environment"
                )
        if self.event_type == "approval_required":
            if (
                self.approval_request_id is None
                or self.action_code not in APPROVAL_ACTIONS
                or self.expires_at is None
                or self.expires_at.tzinfo is None
            ):
                raise NotificationValidationError(
                    "approval event requires persisted request fields"
                )
            expected_environment = {
                "development_plan": "development",
                "merge_pr": "staging",
                "production_deploy": "production",
                "rollback_production": "production",
            }[self.action_code]
            if self.target_environment != expected_environment:
                raise NotificationValidationError(
                    "approval action/environment mismatch"
                )
            if (
                self.action_code == "merge_pr"
                and self.pull_request_number is None
            ):
                raise NotificationValidationError(
                    "merge approval requires PR number"
                )
        elif any(
            value is not None
            for value in (
                self.approval_request_id,
                self.action_code,
                self.expires_at,
            )
        ):
            raise NotificationValidationError(
                "approval fields forbidden for status event"
            )


def notification_from_fields(
    fields: dict[str, object],
) -> SupervisionNotification:
    allowed_fields = {
        "event_type",
        "repository",
        "target_sha",
        "pull_request_number",
        "target_environment",
        "approval_request_id",
        "action_code",
        "expires_at",
    }
    unknown = set(fields) - allowed_fields
    if unknown:
        raise NotificationValidationError("unsafe notification field")
    notification = SupervisionNotification(**fields)  # type: ignore[arg-type]
    notification.validate()
    return notification


def format_notification(notification: SupervisionNotification) -> str:
    notification.validate()
    labels = {
        "pr_created": "PR 已创建",
        "ci_passed": "CI 已通过",
        "ci_failed": "CI 未通过",
        "staging_started": "Staging 部署已开始",
        "staging_passed": "Staging 验收已通过",
        "staging_failed": "Staging 验收失败",
        "approval_required": "需要人工审批",
    }
    lines = [
        f"Maoxx OS：{labels[notification.event_type]}",
        f"事件：{notification.event_type}",
        f"仓库：{notification.repository}",
        f"目标 SHA：{notification.target_sha}",
    ]
    if notification.pull_request_number is not None:
        lines.append(f"PR：#{notification.pull_request_number}")
    if notification.target_environment is not None:
        lines.append(f"环境：{notification.target_environment}")
    if notification.event_type == "approval_required":
        assert notification.approval_request_id is not None
        assert notification.action_code is not None
        assert notification.expires_at is not None
        lines.extend(
            [
                f"动作：{notification.action_code}",
                f"请求：{notification.approval_request_id}",
                f"过期时间：{notification.expires_at.isoformat()}",
                "回复：批准 <request-uuid> 或 拒绝 <request-uuid>",
                "此审批只记录决定，不会执行受保护动作。",
            ]
        )
    return "\n".join(lines)
