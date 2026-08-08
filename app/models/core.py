from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
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
            "'production_deploy', 'rollback_production')",
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
            "(action_code IN ('production_deploy', "
            "'rollback_production') AND "
            "target_environment = 'production')",
            name="action_target",
        ),
        CheckConstraint(
            "(deployment_id IS NULL AND artifact_digest IS NULL) OR "
            "(deployment_id IS NOT NULL AND "
            "artifact_digest ~ '^sha256:[0-9a-f]{64}$')",
            name="deployment_binding",
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
    deployment_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.deployment_intents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    artifact_digest: Mapped[str | None] = mapped_column(
        String(71),
        nullable=True,
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


class DeploymentIntent(Base):
    __tablename__ = "deployment_intents"
    __table_args__ = (
        CheckConstraint(
            "intent_kind IN ('deploy', 'rollback')",
            name="intent_kind",
        ),
        CheckConstraint(
            "action_code IN ('production_deploy', 'rollback_production')",
            name="action_code",
        ),
        CheckConstraint(
            "(intent_kind = 'deploy' AND action_code = 'production_deploy') "
            "OR (intent_kind = 'rollback' AND "
            "action_code = 'rollback_production')",
            name="kind_action",
        ),
        CheckConstraint(
            "target_environment = 'production'",
            name="environment",
        ),
        CheckConstraint(
            "target_sha ~ '^[0-9a-f]{40}$'",
            name="target_sha",
        ),
        CheckConstraint(
            "artifact_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="artifact_digest",
        ),
        CheckConstraint(
            "requested_by_identity_fingerprint ~ '^[0-9a-f]{64}$'",
            name="requester_fingerprint",
        ),
        CheckConstraint(
            "config_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="config_fingerprint",
        ),
        CheckConstraint(
            "migration_risk IN ('NONE', 'LOW', 'MEDIUM', 'HIGH')",
            name="migration_risk",
        ),
        CheckConstraint(
            "migration_risk <> 'MEDIUM' OR "
            "(migration_revision IS NOT NULL AND "
            "rollback_runbook_ref IS NOT NULL)",
            name="medium_migration_evidence",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_deployment_intents_idempotency_key",
        ),
        Index(
            "ix_deployment_intents_target",
            "repository",
            "target_sha",
            "created_at",
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
    intent_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    action_code: Mapped[str] = mapped_column(String(32), nullable=False)
    repository: Mapped[str] = mapped_column(String(200), nullable=False)
    target_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    target_environment: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    artifact_digest: Mapped[str] = mapped_column(
        String(71),
        nullable=False,
    )
    requested_by_identity_fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    service_set: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    migration_risk: Mapped[str] = mapped_column(String(16), nullable=False)
    migration_revision: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
    rollback_runbook_ref: Mapped[str | None] = mapped_column(
        String(300),
        nullable=True,
    )
    config_fingerprint: Mapped[str] = mapped_column(
        String(71),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class DeploymentArtifact(Base):
    __tablename__ = "deployment_artifacts"
    __table_args__ = (
        CheckConstraint(
            "digest ~ '^sha256:[0-9a-f]{64}$'",
            name="digest",
        ),
        CheckConstraint(
            "target_sha ~ '^[0-9a-f]{40}$' AND ci_head_sha ~ '^[0-9a-f]{40}$'",
            name="sha",
        ),
        CheckConstraint("ci_run_id > 0", name="ci_run_id"),
        CheckConstraint("ci_event = 'push'", name="ci_event"),
        CheckConstraint("ci_status = 'completed'", name="ci_status"),
        CheckConstraint("ci_conclusion = 'success'", name="ci_conclusion"),
        CheckConstraint(
            "quality_gate_conclusion = 'success'",
            name="quality_gate",
        ),
        CheckConstraint(
            "ci_provenance_source = 'github_checks_api'",
            name="ci_provenance_source",
        ),
        UniqueConstraint(
            "deployment_id",
            name="uq_deployment_artifacts_deployment_id",
        ),
        Index("ix_deployment_artifacts_digest", "digest"),
        {"schema": "core"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    deployment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.deployment_intents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    repository: Mapped[str] = mapped_column(String(200), nullable=False)
    target_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    digest: Mapped[str] = mapped_column(String(71), nullable=False)
    image_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    ci_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ci_event: Mapped[str] = mapped_column(String(32), nullable=False)
    ci_status: Mapped[str] = mapped_column(String(32), nullable=False)
    ci_conclusion: Mapped[str] = mapped_column(String(32), nullable=False)
    ci_head_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    quality_gate_conclusion: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    protected_main_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )
    ci_workflow_name: Mapped[str] = mapped_column(String(200), nullable=False)
    ci_repository: Mapped[str] = mapped_column(String(200), nullable=False)
    ci_verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ci_provenance_source: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class DeploymentStateEvent(Base):
    __tablename__ = "deployment_state_events"
    __table_args__ = (
        CheckConstraint("sequence > 0", name="sequence"),
        CheckConstraint(
            "actor_identity_fingerprint ~ '^[0-9a-f]{64}$'",
            name="actor_fingerprint",
        ),
        UniqueConstraint(
            "deployment_id",
            "sequence",
            name="uq_deployment_state_events_sequence",
        ),
        Index(
            "ix_deployment_state_events_deployment_occurred",
            "deployment_id",
            "occurred_at",
        ),
        {"schema": "core"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    deployment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.deployment_intents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_state: Mapped[str] = mapped_column(String(64), nullable=False)
    event_code: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_identity_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StagingAcceptance(Base):
    __tablename__ = "staging_acceptances"
    __table_args__ = (
        CheckConstraint("environment = 'staging'", name="environment"),
        CheckConstraint(
            "target_sha ~ '^[0-9a-f]{40}$'",
            name="target_sha",
        ),
        CheckConstraint(
            "artifact_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="artifact_digest",
        ),
        CheckConstraint(
            "accepted_by_identity_fingerprint ~ '^[0-9a-f]{64}$'",
            name="actor_fingerprint",
        ),
        CheckConstraint(
            "config_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="config_fingerprint",
        ),
        CheckConstraint("valid_until > completed_at", name="validity"),
        UniqueConstraint(
            "deployment_id",
            name="uq_staging_acceptances_deployment_id",
        ),
        {"schema": "core"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    deployment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.deployment_intents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    repository: Mapped[str] = mapped_column(String(200), nullable=False)
    target_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    validation_suite_version: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    config_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    accepted_by_identity_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    valid_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class StagingAcceptanceInvalidation(Base):
    __tablename__ = "staging_acceptance_invalidations"
    __table_args__ = (
        UniqueConstraint(
            "acceptance_id",
            name="uq_staging_acceptance_invalidations_acceptance_id",
        ),
        {"schema": "core"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    acceptance_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.staging_acceptances.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    invalidated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DeploymentApprovalBinding(Base):
    __tablename__ = "deployment_approval_bindings"
    __table_args__ = (
        CheckConstraint(
            "target_sha ~ '^[0-9a-f]{40}$'",
            name="target_sha",
        ),
        CheckConstraint(
            "artifact_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="artifact_digest",
        ),
        CheckConstraint(
            "requester_identity_fingerprint ~ '^[0-9a-f]{64}$'",
            name="requester_fingerprint",
        ),
        CheckConstraint(
            "target_environment = 'production'",
            name="environment",
        ),
        CheckConstraint(
            "action_code IN ('production_deploy', 'rollback_production')",
            name="action_code",
        ),
        UniqueConstraint(
            "deployment_id",
            name="uq_deployment_approval_bindings_deployment_id",
        ),
        UniqueConstraint(
            "approval_request_id",
            name="uq_deployment_approval_bindings_request_id",
        ),
        {"schema": "core"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    deployment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.deployment_intents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    approval_request_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.approval_requests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    repository: Mapped[str] = mapped_column(String(200), nullable=False)
    target_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    target_environment: Mapped[str] = mapped_column(String(32), nullable=False)
    action_code: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    requester_identity_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    requester_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DeploymentApprovalConsumption(Base):
    __tablename__ = "deployment_approval_consumptions"
    __table_args__ = (
        UniqueConstraint(
            "deployment_id",
            name="uq_deployment_approval_consumptions_deployment_id",
        ),
        UniqueConstraint(
            "approval_request_id",
            name="uq_deployment_approval_consumptions_request_id",
        ),
        UniqueConstraint(
            "approval_decision_id",
            name="uq_deployment_approval_consumptions_decision_id",
        ),
        {"schema": "core"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    deployment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.deployment_intents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    approval_request_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.approval_requests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    approval_decision_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.approval_decisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    state_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.deployment_state_events.id", ondelete="RESTRICT"),
        nullable=False,
    )
    consumed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DeploymentLock(Base):
    __tablename__ = "deployment_locks"
    __table_args__ = (
        CheckConstraint(
            "environment = 'production'",
            name="environment",
        ),
        CheckConstraint(
            "lease_expires_at > acquired_at",
            name="lease",
        ),
        CheckConstraint(
            "conflict_domain = 'production:global'",
            name="canonical_conflict_domain",
        ),
        CheckConstraint("fencing_token > 0", name="fencing_token"),
        {"schema": "core"},
    )

    conflict_domain: Mapped[str] = mapped_column(String(100), primary_key=True)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    deployment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.deployment_intents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    owner_identity: Mapped[str] = mapped_column(String(200), nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    renewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    lease_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    stale_reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stale_reconciled_token: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    stale_reconciliation_evidence_key: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )


class DeploymentExecutionAttempt(Base):
    __tablename__ = "deployment_execution_attempts"
    __table_args__ = (
        CheckConstraint("fencing_token > 0", name="fencing_token"),
        UniqueConstraint(
            "deployment_id",
            name="uq_deployment_execution_attempts_deployment_id",
        ),
        UniqueConstraint(
            "execution_key",
            name="uq_deployment_execution_attempts_execution_key",
        ),
        {"schema": "core"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    deployment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.deployment_intents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    execution_key: Mapped[str] = mapped_column(String(200), nullable=False)
    executor_identity: Mapped[str] = mapped_column(String(200), nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DeploymentEvidence(Base):
    __tablename__ = "deployment_evidence"
    __table_args__ = (
        CheckConstraint(
            "status_code IN ('passed', 'failed', 'recorded')",
            name="status_code",
        ),
        UniqueConstraint(
            "deployment_id",
            "evidence_type",
            "evidence_key",
            name="uq_deployment_evidence_identity",
        ),
        Index(
            "ix_deployment_evidence_deployment_occurred",
            "deployment_id",
            "occurred_at",
        ),
        {"schema": "core"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    deployment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.deployment_intents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    evidence_type: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status_code: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DeploymentRollback(Base):
    __tablename__ = "deployment_rollbacks"
    __table_args__ = (
        CheckConstraint(
            "current_production_sha ~ '^[0-9a-f]{40}$' AND "
            "rollback_target_sha ~ '^[0-9a-f]{40}$'",
            name="sha",
        ),
        CheckConstraint(
            "rollback_artifact_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="artifact_digest",
        ),
        UniqueConstraint(
            "deployment_id",
            name="uq_deployment_rollbacks_deployment_id",
        ),
        {"schema": "core"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    deployment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.deployment_intents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    failed_deployment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("core.deployment_intents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    current_production_sha: Mapped[str] = mapped_column(
        String(40), nullable=False
    )
    rollback_target_sha: Mapped[str] = mapped_column(
        String(40), nullable=False
    )
    rollback_artifact_digest: Mapped[str] = mapped_column(
        String(71), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
