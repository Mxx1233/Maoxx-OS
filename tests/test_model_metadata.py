import unittest

from sqlalchemy import Index, UniqueConstraint

from app.models import (
    ApprovalDecision,
    ApprovalRequest,
    DeploymentApprovalBinding,
    DeploymentApprovalConsumption,
    DeploymentArtifact,
    DeploymentEvidence,
    DeploymentIntent,
    Entity,
    RawInput,
)


class CoreModelMetadataTests(unittest.TestCase):
    def test_entity_uses_only_migration_0001_composite_index(self) -> None:
        indexes = {
            index.name: tuple(column.name for column in index.columns)
            for index in Entity.__table__.indexes
        }

        self.assertEqual(
            indexes,
            {
                "ix_entities_user_type_status": (
                    "user_id",
                    "entity_type",
                    "status_code",
                )
            },
        )

    def test_raw_input_uses_only_migration_0001_composite_index(self) -> None:
        indexes = {
            index.name: tuple(column.name for column in index.columns)
            for index in RawInput.__table__.indexes
            if isinstance(index, Index)
        }

        self.assertEqual(
            indexes,
            {
                "ix_raw_inputs_user_received": (
                    "user_id",
                    "received_at",
                )
            },
        )

    def test_raw_input_declares_message_deduplication_constraint(self) -> None:
        unique_constraints = {
            constraint.name: tuple(
                column.name for column in constraint.columns
            )
            for constraint in RawInput.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        }

        self.assertEqual(
            unique_constraints["uq_raw_inputs_channel_message"],
            ("channel_code", "external_message_id"),
        )

    def test_approval_models_declare_idempotency_and_single_decision(
        self,
    ) -> None:
        request_constraints = {
            constraint.name: tuple(
                column.name for column in constraint.columns
            )
            for constraint in ApprovalRequest.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        decision_constraints = {
            constraint.name: tuple(
                column.name for column in constraint.columns
            )
            for constraint in ApprovalDecision.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        self.assertEqual(
            request_constraints["uq_approval_requests_idempotency_key"],
            ("idempotency_key",),
        )
        self.assertEqual(
            decision_constraints["uq_approval_decisions_request_id"],
            ("request_id",),
        )
        self.assertEqual(
            decision_constraints["uq_approval_decisions_feishu_event_id"],
            ("feishu_event_id",),
        )

    def test_deployment_models_declare_replay_and_consumption_uniqueness(
        self,
    ) -> None:
        expected = {
            DeploymentIntent: {
                "uq_deployment_intents_idempotency_key": ("idempotency_key",),
            },
            DeploymentArtifact: {
                "uq_deployment_artifacts_deployment_id": ("deployment_id",),
            },
            DeploymentApprovalBinding: {
                "uq_deployment_approval_bindings_deployment_id": (
                    "deployment_id",
                ),
                "uq_deployment_approval_bindings_request_id": (
                    "approval_request_id",
                ),
            },
            DeploymentApprovalConsumption: {
                "uq_deployment_approval_consumptions_deployment_id": (
                    "deployment_id",
                ),
                "uq_deployment_approval_consumptions_request_id": (
                    "approval_request_id",
                ),
                "uq_deployment_approval_consumptions_decision_id": (
                    "approval_decision_id",
                ),
            },
            DeploymentEvidence: {
                "uq_deployment_evidence_identity": (
                    "deployment_id",
                    "evidence_type",
                    "evidence_key",
                ),
            },
        }

        for model, model_expected in expected.items():
            with self.subTest(table=model.__tablename__):
                constraints = {
                    constraint.name: tuple(
                        column.name for column in constraint.columns
                    )
                    for constraint in model.__table__.constraints
                    if isinstance(constraint, UniqueConstraint)
                }
                for name, columns in model_expected.items():
                    self.assertEqual(constraints[name], columns)


if __name__ == "__main__":
    unittest.main()
