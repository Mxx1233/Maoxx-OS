from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "core"}

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    display_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="Europe/Berlin",
    )
    locale: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="zh-CN",
    )
    default_units: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    privacy_settings: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (
        Index(
            "ix_entities_user_type_status",
            "user_id",
            "entity_type",
            "status_code",
        ),
        {"schema": "core"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    title: Mapped[str | None] = mapped_column(
        String(300),
        nullable=True,
    )
    status_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    source_code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="system",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    metadata_json: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class RawInput(Base):
    __tablename__ = "raw_inputs"
    __table_args__ = (
        UniqueConstraint(
            "channel_code",
            "external_message_id",
            name="uq_raw_inputs_channel_message",
        ),
        Index(
            "ix_raw_inputs_user_received",
            "user_id",
            "received_at",
        ),
        {"schema": "core"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    channel_code: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    external_message_id: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
    input_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    text_content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    processing_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
    )
    metadata_json: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(
            "action_code IN ('development_plan', 'merge_pr', "
            "'production_deploy')",
            name="action_code",
        ),
        CheckConstraint(
            "target_environment IN ('development', 'staging', 'production')",
            name="target_environment",
        ),
        CheckConstraint(
            "target_sha ~ '^[0-9a-f]{40}$'",
            name="target_sha",
        ),
        CheckConstraint(
            "pull_request_number IS NULL OR pull_request_number > 0",
            name="pr_number",
        ),
        CheckConstraint(
            "expires_at > requested_at",
            name="expiry",
        ),
        CheckConstraint(
            "(action_code = 'development_plan' AND "
            "target_environment = 'development') OR "
            "(action_code = 'merge_pr' AND "
            "target_environment = 'staging' AND "
            "pull_request_number IS NOT NULL) OR "
            "(action_code = 'production_deploy' AND "
            "target_environment = 'production')",
            name="action_target",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_approval_requests_idempotency_key",
        ),
        Index(
            "ix_approval_requests_user_requested",
            "user_id",
            "requested_at",
        ),
        Index(
            "ix_approval_requests_target",
            "action_code",
            "target_sha",
            "expires_at",
        ),
        {"schema": "core"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    action_code: Mapped[str] = mapped_column(String(32), nullable=False)
    repository: Mapped[str] = mapped_column(String(200), nullable=False)
    pull_request_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    target_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    target_environment: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class ApprovalDecision(Base):
    __tablename__ = "approval_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision_code IN ('approved', 'rejected')",
            name="decision_code",
        ),
        CheckConstraint(
            "approver_identity_fingerprint ~ '^[0-9a-f]{64}$'",
            name="identity_fingerprint",
        ),
        UniqueConstraint(
            "request_id",
            name="uq_approval_decisions_request_id",
        ),
        UniqueConstraint(
            "feishu_event_id",
            name="uq_approval_decisions_feishu_event_id",
        ),
        Index(
            "ix_approval_decisions_actor_decided",
            "actor_user_id",
            "decided_at",
        ),
        {"schema": "core"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    request_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.approval_requests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    decision_code: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    approver_identity_fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    feishu_event_id: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
