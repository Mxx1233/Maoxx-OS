"""Add Phase 1D-E approval audit tables.

Revision ID: 0002_phase_1d_e_approvals
Revises: 0001_core_foundation
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0002_phase_1d_e_approvals"
down_revision: Union[str, None] = "0001_core_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_code", sa.String(length=32), nullable=False),
        sa.Column("repository", sa.String(length=200), nullable=False),
        sa.Column("pull_request_number", sa.Integer(), nullable=True),
        sa.Column("target_sha", sa.String(length=40), nullable=False),
        sa.Column(
            "target_environment",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "idempotency_key",
            sa.String(length=200),
            nullable=False,
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action_code IN ('development_plan', 'merge_pr', "
            "'production_deploy')",
            name="action_code",
        ),
        sa.CheckConstraint(
            "target_environment IN ('development', 'staging', "
            "'production')",
            name="target_environment",
        ),
        sa.CheckConstraint(
            "target_sha ~ '^[0-9a-f]{40}$'",
            name="target_sha",
        ),
        sa.CheckConstraint(
            "pull_request_number IS NULL OR pull_request_number > 0",
            name="pr_number",
        ),
        sa.CheckConstraint(
            "expires_at > requested_at",
            name="expiry",
        ),
        sa.CheckConstraint(
            "(action_code = 'development_plan' AND "
            "target_environment = 'development') OR "
            "(action_code = 'merge_pr' AND "
            "target_environment = 'staging' AND "
            "pull_request_number IS NOT NULL) OR "
            "(action_code = 'production_deploy' AND "
            "target_environment = 'production')",
            name="action_target",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["core.users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_approval_requests_idempotency_key",
        ),
        schema="core",
    )
    op.create_index(
        "ix_approval_requests_user_requested",
        "approval_requests",
        ["user_id", "requested_at"],
        schema="core",
    )
    op.create_index(
        "ix_approval_requests_target",
        "approval_requests",
        ["action_code", "target_sha", "expires_at"],
        schema="core",
    )

    op.create_table(
        "approval_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_code", sa.String(length=16), nullable=False),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "approver_identity_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("feishu_event_id", sa.String(length=200), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "decision_code IN ('approved', 'rejected')",
            name="decision_code",
        ),
        sa.CheckConstraint(
            "approver_identity_fingerprint ~ '^[0-9a-f]{64}$'",
            name="identity_fingerprint",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["core.users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["core.approval_requests.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_id",
            name="uq_approval_decisions_request_id",
        ),
        sa.UniqueConstraint(
            "feishu_event_id",
            name="uq_approval_decisions_feishu_event_id",
        ),
        schema="core",
    )
    op.create_index(
        "ix_approval_decisions_actor_decided",
        "approval_decisions",
        ["actor_user_id", "decided_at"],
        schema="core",
    )

    op.execute(
        """
        CREATE FUNCTION core.reject_approval_decision_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'approval decisions are append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_approval_decisions_append_only
        BEFORE UPDATE OR DELETE ON core.approval_decisions
        FOR EACH ROW
        EXECUTE FUNCTION core.reject_approval_decision_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_approval_decisions_append_only "
        "ON core.approval_decisions"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS core.reject_approval_decision_mutation()"
    )
    op.drop_index(
        "ix_approval_decisions_actor_decided",
        table_name="approval_decisions",
        schema="core",
    )
    op.drop_table("approval_decisions", schema="core")
    op.drop_index(
        "ix_approval_requests_target",
        table_name="approval_requests",
        schema="core",
    )
    op.drop_index(
        "ix_approval_requests_user_requested",
        table_name="approval_requests",
        schema="core",
    )
    op.drop_table("approval_requests", schema="core")
