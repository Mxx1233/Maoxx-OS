from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from sqlalchemy import select

from app.models import DeploymentEvidence, DeploymentIntent
from app.services.deployment_policy import DIGEST_PATTERN, SHA_PATTERN
from app.services.deployment_service import (
    DeploymentGateError,
    record_deployment_evidence,
)


DEPLOYMENT_NOTIFICATION_EVENTS = frozenset(
    {
        "staging_started",
        "staging_passed",
        "staging_failed",
        "production_approval_required",
        "production_started",
        "production_passed",
        "production_failed",
        "rollback_approval_required",
        "rollback_started",
        "rollback_passed",
        "rollback_failed",
    }
)


class DeploymentNotificationSink(Protocol):
    def send(self, text: str, *, idempotency_key: str) -> str: ...


@dataclass(frozen=True)
class DeploymentNotification:
    event_type: str
    deployment_id: UUID
    repository: str
    target_sha: str
    artifact_digest: str
    environment: str = "production"

    def validate(self) -> None:
        if self.event_type not in DEPLOYMENT_NOTIFICATION_EVENTS:
            raise ValueError("unsupported deployment notification")
        if not SHA_PATTERN.fullmatch(self.target_sha):
            raise ValueError("invalid notification SHA")
        if not DIGEST_PATTERN.fullmatch(self.artifact_digest):
            raise ValueError("invalid notification digest")
        if self.environment != "production":
            raise ValueError("invalid notification environment")

    def text(self) -> str:
        self.validate()
        return "\n".join(
            (
                "Maoxx OS Controlled Deployment",
                f"事件：{self.event_type}",
                f"部署：{self.deployment_id}",
                f"仓库：{self.repository}",
                f"目标 SHA：{self.target_sha}",
                f"工件摘要：{self.artifact_digest}",
                f"环境：{self.environment}",
                "此通知不代表任何未记录的受保护动作已执行。",
            )
        )


def send_deployment_notification(
    db: Session,
    *,
    deployment_id: UUID,
    event_type: str,
    evidence_key: str,
    sink: DeploymentNotificationSink,
) -> str:
    intent = db.get(DeploymentIntent, deployment_id)
    if intent is None:
        raise DeploymentGateError("unknown_deployment")
    existing = db.execute(
        select(DeploymentEvidence).where(
            DeploymentEvidence.deployment_id == deployment_id,
            DeploymentEvidence.evidence_type == "feishu_notification",
            DeploymentEvidence.evidence_key == evidence_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        reference = existing.payload.get("reference")
        if not isinstance(reference, str) or not reference:
            raise DeploymentGateError("invalid_notification_evidence")
        return reference
    notification = DeploymentNotification(
        event_type=event_type,
        deployment_id=deployment_id,
        repository=intent.repository,
        target_sha=intent.target_sha,
        artifact_digest=intent.artifact_digest,
    )
    reference = sink.send(notification.text(), idempotency_key=evidence_key)
    if not reference or len(reference) > 200:
        raise DeploymentGateError("invalid_notification_reference")
    record_deployment_evidence(
        db,
        deployment_id=deployment_id,
        evidence_type="feishu_notification",
        evidence_key=evidence_key,
        status_code="recorded",
        payload={"event_type": event_type, "reference": reference},
    )
    return reference
