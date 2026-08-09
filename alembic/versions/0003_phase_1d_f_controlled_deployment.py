"""Add Phase 1D-F controlled deployment records.

Revision ID: 0003_phase_1d_f_deployment
Revises: 0002_phase_1d_e_approvals
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0003_phase_1d_f_deployment"
down_revision: Union[str, None] = "0002_phase_1d_e_approvals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


AUDIT_TABLES = (
    "deployment_intents",
    "deployment_artifacts",
    "deployment_state_events",
    "staging_acceptances",
    "staging_acceptance_invalidations",
    "deployment_approval_bindings",
    "deployment_approval_consumptions",
    "deployment_execution_attempts",
    "deployment_evidence",
    "deployment_rollbacks",
)


def upgrade() -> None:
    op.create_table(
        "principals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("principal_type", sa.String(length=16), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("identity_key", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "principal_type IN ('HUMAN', 'SERVICE', 'EXECUTOR')",
            name=op.f("ck_principals_type"),
        ),
        sa.CheckConstraint(
            "(principal_type = 'HUMAN' AND user_id IS NOT NULL) OR "
            "(principal_type IN ('SERVICE', 'EXECUTOR') AND user_id IS NULL)",
            name=op.f("ck_principals_human_user_binding"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["core.users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_principals")),
        sa.UniqueConstraint("user_id", name=op.f("uq_principals_user_id")),
        sa.UniqueConstraint("identity_key", name=op.f("uq_principals_identity_key")),
        schema="core",
    )
    op.create_table(
        "external_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("tenant_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("subject_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mapping_version", sa.Integer(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("audit_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint("mapping_version > 0", name=op.f("ck_external_identities_mapping_version")),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name=op.f("ck_external_identities_validity")),
        sa.ForeignKeyConstraint(["principal_id"], ["core.principals.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_identities")),
        sa.UniqueConstraint("provider", "tenant_fingerprint", "subject_fingerprint", "mapping_version", name=op.f("uq_external_identities_mapping_version")),
        schema="core",
    )
    op.create_index(
        "uq_external_identities_active_subject",
        "external_identities",
        ["provider", "tenant_fingerprint", "subject_fingerprint"],
        unique=True,
        schema="core",
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.execute(
        """
        CREATE FUNCTION core.protect_external_identity_history()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'external identity history is append-only';
            END IF;
            IF OLD.valid_to IS NOT NULL OR NEW.valid_to IS NULL
               OR NEW.valid_to <= OLD.valid_from
               OR ROW(NEW.id, NEW.provider, NEW.tenant_fingerprint,
                      NEW.subject_fingerprint, NEW.principal_id,
                      NEW.mapping_version, NEW.valid_from,
                      NEW.audit_event_id)
                  IS DISTINCT FROM
                  ROW(OLD.id, OLD.provider, OLD.tenant_fingerprint,
                      OLD.subject_fingerprint, OLD.principal_id,
                      OLD.mapping_version, OLD.valid_from,
                      OLD.audit_event_id) THEN
                RAISE EXCEPTION 'external identity history is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_external_identities_history
        BEFORE UPDATE OR DELETE ON core.external_identities
        FOR EACH ROW EXECUTE FUNCTION core.protect_external_identity_history()
        """
    )
    op.add_column(
        "approval_decisions",
        sa.Column("actor_external_identity_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="core",
    )
    op.create_foreign_key(
        op.f("fk_approval_decisions_actor_external_identity_id_external_identities"),
        "approval_decisions",
        "external_identities",
        ["actor_external_identity_id"],
        ["id"],
        source_schema="core",
        referent_schema="core",
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        op.f("ck_approval_requests_action_code"),
        "approval_requests",
        schema="core",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_approval_requests_action_target"),
        "approval_requests",
        schema="core",
        type_="check",
    )
    op.create_check_constraint(
        "action_code",
        "approval_requests",
        "action_code IN ('development_plan', 'merge_pr', "
        "'production_deploy', 'rollback_production')",
        schema="core",
    )
    op.create_check_constraint(
        "action_target",
        "approval_requests",
        "(action_code = 'development_plan' AND "
        "target_environment = 'development') OR "
        "(action_code = 'merge_pr' AND target_environment = 'staging' "
        "AND pull_request_number IS NOT NULL) OR "
        "(action_code IN ('production_deploy', 'rollback_production') "
        "AND target_environment = 'production')",
        schema="core",
    )

    op.create_table(
        "deployment_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requester_principal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requester_external_identity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("intent_kind", sa.String(length=16), nullable=False),
        sa.Column("action_code", sa.String(length=32), nullable=False),
        sa.Column("repository", sa.String(length=200), nullable=False),
        sa.Column("target_sha", sa.String(length=40), nullable=False),
        sa.Column("target_environment", sa.String(length=32), nullable=False),
        sa.Column("artifact_digest", sa.String(length=71), nullable=False),
        sa.Column(
            "requested_by_identity_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("service_set", postgresql.JSONB(), nullable=False),
        sa.Column("migration_risk", sa.String(length=16), nullable=False),
        sa.Column("migration_revision", sa.String(length=200), nullable=True),
        sa.Column(
            "rollback_runbook_ref", sa.String(length=300), nullable=True
        ),
        sa.Column("config_fingerprint", sa.String(length=71), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "intent_kind IN ('deploy', 'rollback')",
            name=op.f("ck_deployment_intents_intent_kind"),
        ),
        sa.CheckConstraint(
            "action_code IN ('production_deploy', 'rollback_production')",
            name=op.f("ck_deployment_intents_action_code"),
        ),
        sa.CheckConstraint(
            "(intent_kind = 'deploy' AND action_code = 'production_deploy') "
            "OR (intent_kind = 'rollback' AND "
            "action_code = 'rollback_production')",
            name=op.f("ck_deployment_intents_kind_action"),
        ),
        sa.CheckConstraint(
            "target_environment = 'production'",
            name=op.f("ck_deployment_intents_environment"),
        ),
        sa.CheckConstraint(
            "target_sha ~ '^[0-9a-f]{40}$'",
            name=op.f("ck_deployment_intents_target_sha"),
        ),
        sa.CheckConstraint(
            "artifact_digest ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_deployment_intents_artifact_digest"),
        ),
        sa.CheckConstraint(
            "requested_by_identity_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_deployment_intents_requester_fingerprint"),
        ),
        sa.CheckConstraint(
            "config_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_deployment_intents_config_fingerprint"),
        ),
        sa.CheckConstraint(
            "migration_risk IN ('NONE', 'LOW', 'MEDIUM', 'HIGH')",
            name=op.f("ck_deployment_intents_migration_risk"),
        ),
        sa.CheckConstraint(
            "migration_risk <> 'MEDIUM' OR "
            "(migration_revision IS NOT NULL AND "
            "rollback_runbook_ref IS NOT NULL)",
            name=op.f("ck_deployment_intents_medium_migration_evidence"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["core.users.id"],
            name=op.f("fk_deployment_intents_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["requester_principal_id"], ["core.principals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requester_external_identity_id"], ["core.external_identities.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deployment_intents")),
        sa.UniqueConstraint(
            "idempotency_key",
            name=op.f("uq_deployment_intents_idempotency_key"),
        ),
        schema="core",
    )
    op.create_index(
        "ix_deployment_intents_target",
        "deployment_intents",
        ["repository", "target_sha", "created_at"],
        schema="core",
    )

    op.add_column(
        "approval_requests",
        sa.Column(
            "deployment_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        schema="core",
    )
    op.add_column(
        "approval_requests",
        sa.Column("artifact_digest", sa.String(length=71), nullable=True),
        schema="core",
    )
    op.create_foreign_key(
        op.f("fk_approval_requests_deployment_id_deployment_intents"),
        "approval_requests",
        "deployment_intents",
        ["deployment_id"],
        ["id"],
        source_schema="core",
        referent_schema="core",
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "deployment_binding",
        "approval_requests",
        "(deployment_id IS NULL AND artifact_digest IS NULL) OR "
        "(deployment_id IS NOT NULL AND "
        "artifact_digest ~ '^sha256:[0-9a-f]{64}$')",
        schema="core",
    )

    op.create_table(
        "deployment_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "deployment_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("repository", sa.String(length=200), nullable=False),
        sa.Column("target_sha", sa.String(length=40), nullable=False),
        sa.Column("digest", sa.String(length=71), nullable=False),
        sa.Column("image_reference", sa.String(length=500), nullable=False),
        sa.Column("service_images", postgresql.JSONB(), nullable=False),
        sa.Column("ci_run_id", sa.BigInteger(), nullable=False),
        sa.Column("ci_event", sa.String(length=32), nullable=False),
        sa.Column("ci_status", sa.String(length=32), nullable=False),
        sa.Column("ci_conclusion", sa.String(length=32), nullable=False),
        sa.Column("ci_head_sha", sa.String(length=40), nullable=False),
        sa.Column(
            "quality_gate_conclusion", sa.String(length=32), nullable=False
        ),
        sa.Column("protected_main_verified", sa.Boolean(), nullable=False),
        sa.Column("ci_workflow_name", sa.String(length=200), nullable=False),
        sa.Column("ci_repository", sa.String(length=200), nullable=False),
        sa.Column(
            "ci_verified_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "ci_provenance_source", sa.String(length=64), nullable=False
        ),
        sa.Column("provenance", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "digest ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_deployment_artifacts_digest"),
        ),
        sa.CheckConstraint(
            "target_sha ~ '^[0-9a-f]{40}$' AND ci_head_sha ~ '^[0-9a-f]{40}$'",
            name=op.f("ck_deployment_artifacts_sha"),
        ),
        sa.CheckConstraint(
            "ci_run_id > 0",
            name=op.f("ck_deployment_artifacts_ci_run_id"),
        ),
        sa.CheckConstraint(
            "ci_event = 'push'",
            name=op.f("ck_deployment_artifacts_ci_event"),
        ),
        sa.CheckConstraint(
            "ci_status = 'completed'",
            name=op.f("ck_deployment_artifacts_ci_status"),
        ),
        sa.CheckConstraint(
            "ci_conclusion = 'success'",
            name=op.f("ck_deployment_artifacts_ci_conclusion"),
        ),
        sa.CheckConstraint(
            "quality_gate_conclusion = 'success'",
            name=op.f("ck_deployment_artifacts_quality_gate"),
        ),
        sa.CheckConstraint(
            "ci_provenance_source = 'github_checks_api'",
            name=op.f("ck_deployment_artifacts_ci_provenance_source"),
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["core.deployment_intents.id"],
            name=op.f(
                "fk_deployment_artifacts_deployment_id_deployment_intents"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deployment_artifacts")),
        sa.UniqueConstraint(
            "deployment_id",
            name=op.f("uq_deployment_artifacts_deployment_id"),
        ),
        schema="core",
    )
    op.create_index(
        "ix_deployment_artifacts_digest",
        "deployment_artifacts",
        ["digest"],
        schema="core",
    )

    op.create_table(
        "deployment_state_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "deployment_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.String(length=64), nullable=True),
        sa.Column("to_state", sa.String(length=64), nullable=False),
        sa.Column("event_code", sa.String(length=64), nullable=False),
        sa.Column(
            "actor_identity_fingerprint", sa.String(length=64), nullable=False
        ),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sequence > 0",
            name=op.f("ck_deployment_state_events_sequence"),
        ),
        sa.CheckConstraint(
            "actor_identity_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_deployment_state_events_actor_fingerprint"),
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["core.deployment_intents.id"],
            name=op.f(
                "fk_deployment_state_events_deployment_id_deployment_intents"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deployment_state_events")),
        sa.UniqueConstraint(
            "deployment_id",
            "sequence",
            name=op.f("uq_deployment_state_events_sequence"),
        ),
        schema="core",
    )
    op.create_index(
        "ix_deployment_state_events_deployment_occurred",
        "deployment_state_events",
        ["deployment_id", "occurred_at"],
        schema="core",
    )

    op.create_table(
        "staging_acceptances",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "deployment_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("repository", sa.String(length=200), nullable=False),
        sa.Column("target_sha", sa.String(length=40), nullable=False),
        sa.Column("artifact_digest", sa.String(length=71), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column(
            "validation_suite_version", sa.String(length=100), nullable=False
        ),
        sa.Column("config_fingerprint", sa.String(length=71), nullable=False),
        sa.Column(
            "accepted_by_identity_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "environment = 'staging'",
            name=op.f("ck_staging_acceptances_environment"),
        ),
        sa.CheckConstraint(
            "target_sha ~ '^[0-9a-f]{40}$'",
            name=op.f("ck_staging_acceptances_target_sha"),
        ),
        sa.CheckConstraint(
            "artifact_digest ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_staging_acceptances_artifact_digest"),
        ),
        sa.CheckConstraint(
            "accepted_by_identity_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_staging_acceptances_actor_fingerprint"),
        ),
        sa.CheckConstraint(
            "config_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_staging_acceptances_config_fingerprint"),
        ),
        sa.CheckConstraint(
            "valid_until > completed_at",
            name=op.f("ck_staging_acceptances_validity"),
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["core.deployment_intents.id"],
            name=op.f(
                "fk_staging_acceptances_deployment_id_deployment_intents"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staging_acceptances")),
        sa.UniqueConstraint(
            "deployment_id",
            name=op.f("uq_staging_acceptances_deployment_id"),
        ),
        schema="core",
    )

    op.create_table(
        "staging_acceptance_invalidations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "acceptance_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column(
            "invalidated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["acceptance_id"],
            ["core.staging_acceptances.id"],
            name=op.f(
                "fk_staging_acceptance_invalidations_acceptance_id_"
                "staging_acceptances"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_staging_acceptance_invalidations")
        ),
        sa.UniqueConstraint(
            "acceptance_id",
            name=op.f("uq_staging_acceptance_invalidations_acceptance_id"),
        ),
        schema="core",
    )

    op.create_table(
        "deployment_approval_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "deployment_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "approval_request_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("repository", sa.String(length=200), nullable=False),
        sa.Column("target_sha", sa.String(length=40), nullable=False),
        sa.Column("target_environment", sa.String(length=32), nullable=False),
        sa.Column("action_code", sa.String(length=32), nullable=False),
        sa.Column("artifact_digest", sa.String(length=71), nullable=False),
        sa.Column(
            "requester_identity_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "requester_user_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "bound_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "target_sha ~ '^[0-9a-f]{40}$'",
            name=op.f("ck_deployment_approval_bindings_target_sha"),
        ),
        sa.CheckConstraint(
            "artifact_digest ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_deployment_approval_bindings_artifact_digest"),
        ),
        sa.CheckConstraint(
            "requester_identity_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_deployment_approval_bindings_requester_fingerprint"),
        ),
        sa.CheckConstraint(
            "target_environment = 'production'",
            name=op.f("ck_deployment_approval_bindings_environment"),
        ),
        sa.CheckConstraint(
            "action_code IN ('production_deploy', 'rollback_production')",
            name=op.f("ck_deployment_approval_bindings_action_code"),
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["core.deployment_intents.id"],
            name=op.f(
                "fk_deployment_approval_bindings_deployment_id_"
                "deployment_intents"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_request_id"],
            ["core.approval_requests.id"],
            name=op.f(
                "fk_deployment_approval_bindings_approval_request_id_"
                "approval_requests"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requester_user_id"],
            ["core.users.id"],
            name=op.f(
                "fk_deployment_approval_bindings_requester_user_id_users"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_deployment_approval_bindings")
        ),
        sa.UniqueConstraint(
            "deployment_id",
            name=op.f("uq_deployment_approval_bindings_deployment_id"),
        ),
        sa.UniqueConstraint(
            "approval_request_id",
            name=op.f("uq_deployment_approval_bindings_request_id"),
        ),
        schema="core",
    )

    op.create_table(
        "deployment_approval_consumptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "deployment_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "approval_request_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "approval_decision_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "state_event_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["core.deployment_intents.id"],
            name=op.f(
                "fk_deployment_approval_consumptions_deployment_id_"
                "deployment_intents"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_request_id"],
            ["core.approval_requests.id"],
            name=op.f(
                "fk_deployment_approval_consumptions_approval_request_id_"
                "approval_requests"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_decision_id"],
            ["core.approval_decisions.id"],
            name=op.f(
                "fk_deployment_approval_consumptions_approval_decision_id_"
                "approval_decisions"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["state_event_id"],
            ["core.deployment_state_events.id"],
            name=op.f(
                "fk_deployment_approval_consumptions_state_event_id_"
                "deployment_state_events"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_deployment_approval_consumptions")
        ),
        sa.UniqueConstraint(
            "deployment_id",
            name=op.f("uq_deployment_approval_consumptions_deployment_id"),
        ),
        sa.UniqueConstraint(
            "approval_request_id",
            name=op.f("uq_deployment_approval_consumptions_request_id"),
        ),
        sa.UniqueConstraint(
            "approval_decision_id",
            name=op.f("uq_deployment_approval_consumptions_decision_id"),
        ),
        schema="core",
    )

    op.create_table(
        "deployment_locks",
        sa.Column("conflict_domain", sa.String(length=100), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column(
            "deployment_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("owner_identity", sa.String(length=200), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "lease_expires_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "stale_reconciled_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("stale_reconciled_token", sa.BigInteger(), nullable=True),
        sa.Column(
            "stale_reconciliation_evidence_key",
            sa.String(length=200),
            nullable=True,
        ),
        sa.CheckConstraint(
            "environment = 'production'",
            name=op.f("ck_deployment_locks_environment"),
        ),
        sa.CheckConstraint(
            "lease_expires_at > acquired_at",
            name=op.f("ck_deployment_locks_lease"),
        ),
        sa.CheckConstraint(
            "conflict_domain = 'production:global'",
            name=op.f("ck_deployment_locks_canonical_conflict_domain"),
        ),
        sa.CheckConstraint(
            "fencing_token > 0",
            name=op.f("ck_deployment_locks_fencing_token"),
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["core.deployment_intents.id"],
            name=op.f("fk_deployment_locks_deployment_id_deployment_intents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "conflict_domain", name=op.f("pk_deployment_locks")
        ),
        schema="core",
    )

    op.create_table(
        "deployment_execution_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "deployment_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("execution_key", sa.String(length=200), nullable=False),
        sa.Column("executor_identity", sa.String(length=200), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("intent_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "fencing_token > 0",
            name=op.f("ck_deployment_execution_attempts_fencing_token"),
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["core.deployment_intents.id"],
            name=op.f(
                "fk_deployment_execution_attempts_deployment_id_"
                "deployment_intents"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_deployment_execution_attempts")
        ),
        sa.UniqueConstraint(
            "deployment_id",
            name=op.f("uq_deployment_execution_attempts_deployment_id"),
        ),
        sa.UniqueConstraint(
            "execution_key",
            name=op.f("uq_deployment_execution_attempts_execution_key"),
        ),
        schema="core",
    )

    op.create_table(
        "deployment_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "deployment_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("evidence_type", sa.String(length=64), nullable=False),
        sa.Column("evidence_key", sa.String(length=200), nullable=False),
        sa.Column("status_code", sa.String(length=16), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status_code IN ('pending', 'passed', 'failed', 'recorded')",
            name=op.f("ck_deployment_evidence_status_code"),
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["core.deployment_intents.id"],
            name=op.f(
                "fk_deployment_evidence_deployment_id_deployment_intents"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deployment_evidence")),
        sa.UniqueConstraint(
            "deployment_id",
            "evidence_type",
            "evidence_key",
            name=op.f("uq_deployment_evidence_identity"),
        ),
        schema="core",
    )
    op.create_index(
        "ix_deployment_evidence_deployment_occurred",
        "deployment_evidence",
        ["deployment_id", "occurred_at"],
        schema="core",
    )

    op.create_table(
        "deployment_rollbacks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "deployment_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "failed_deployment_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "current_production_sha", sa.String(length=40), nullable=False
        ),
        sa.Column("rollback_target_sha", sa.String(length=40), nullable=False),
        sa.Column(
            "rollback_artifact_digest", sa.String(length=71), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "current_production_sha ~ '^[0-9a-f]{40}$' AND "
            "rollback_target_sha ~ '^[0-9a-f]{40}$'",
            name=op.f("ck_deployment_rollbacks_sha"),
        ),
        sa.CheckConstraint(
            "rollback_artifact_digest ~ '^sha256:[0-9a-f]{64}$'",
            name=op.f("ck_deployment_rollbacks_artifact_digest"),
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"],
            ["core.deployment_intents.id"],
            name=op.f(
                "fk_deployment_rollbacks_deployment_id_deployment_intents"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["failed_deployment_id"],
            ["core.deployment_intents.id"],
            name=op.f(
                "fk_deployment_rollbacks_failed_deployment_id_"
                "deployment_intents"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deployment_rollbacks")),
        sa.UniqueConstraint(
            "deployment_id",
            name=op.f("uq_deployment_rollbacks_deployment_id"),
        ),
        schema="core",
    )

    op.execute(
        """
        CREATE FUNCTION core.reject_deployment_audit_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'deployment audit records are append-only';
        END;
        $$
        """
    )
    for table_name in AUDIT_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_append_only
            BEFORE UPDATE OR DELETE ON core.{table_name}
            FOR EACH ROW
            EXECUTE FUNCTION core.reject_deployment_audit_mutation()
            """
        )


def downgrade() -> None:
    for table_name in reversed(AUDIT_TABLES):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only "
            f"ON core.{table_name}"
        )
    op.execute(
        "DROP FUNCTION IF EXISTS core.reject_deployment_audit_mutation()"
    )
    op.drop_table("deployment_rollbacks", schema="core")
    op.drop_index(
        "ix_deployment_evidence_deployment_occurred",
        table_name="deployment_evidence",
        schema="core",
    )
    op.drop_table("deployment_evidence", schema="core")
    op.drop_table("deployment_execution_attempts", schema="core")
    op.drop_table("deployment_locks", schema="core")
    op.drop_table("deployment_approval_consumptions", schema="core")
    op.drop_table("deployment_approval_bindings", schema="core")
    op.drop_table("staging_acceptance_invalidations", schema="core")
    op.drop_table("staging_acceptances", schema="core")
    op.drop_index(
        "ix_deployment_state_events_deployment_occurred",
        table_name="deployment_state_events",
        schema="core",
    )
    op.drop_table("deployment_state_events", schema="core")
    op.drop_index(
        "ix_deployment_artifacts_digest",
        table_name="deployment_artifacts",
        schema="core",
    )
    op.drop_table("deployment_artifacts", schema="core")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_external_identities_history "
        "ON core.external_identities"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS core.protect_external_identity_history()"
    )
    op.drop_constraint(
        op.f("fk_approval_decisions_actor_external_identity_id_external_identities"),
        "approval_decisions",
        schema="core",
        type_="foreignkey",
    )
    op.drop_column(
        "approval_decisions", "actor_external_identity_id", schema="core"
    )
    op.drop_table("external_identities", schema="core")
    op.drop_table("principals", schema="core")
    op.drop_constraint(
        op.f("ck_approval_requests_deployment_binding"),
        "approval_requests",
        schema="core",
        type_="check",
    )
    op.drop_constraint(
        op.f("fk_approval_requests_deployment_id_deployment_intents"),
        "approval_requests",
        schema="core",
        type_="foreignkey",
    )
    op.drop_column("approval_requests", "artifact_digest", schema="core")
    op.drop_column("approval_requests", "deployment_id", schema="core")
    op.drop_index(
        "ix_deployment_intents_target",
        table_name="deployment_intents",
        schema="core",
    )
    op.drop_table("deployment_intents", schema="core")

    op.drop_constraint(
        op.f("ck_approval_requests_action_target"),
        "approval_requests",
        schema="core",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_approval_requests_action_code"),
        "approval_requests",
        schema="core",
        type_="check",
    )
    op.create_check_constraint(
        "action_code",
        "approval_requests",
        "action_code IN ('development_plan', 'merge_pr', 'production_deploy')",
        schema="core",
    )
    op.create_check_constraint(
        "action_target",
        "approval_requests",
        "(action_code = 'development_plan' AND "
        "target_environment = 'development') OR "
        "(action_code = 'merge_pr' AND target_environment = 'staging' "
        "AND pull_request_number IS NOT NULL) OR "
        "(action_code = 'production_deploy' AND "
        "target_environment = 'production')",
        schema="core",
    )
