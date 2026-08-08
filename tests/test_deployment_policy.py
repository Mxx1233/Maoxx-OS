import unittest
from uuid import uuid4

from app.services.deployment_executor import ControlledExecutionBoundary
from app.services.deployment_notifications import DeploymentNotification
from app.services.deployment_policy import (
    DeploymentPolicyError,
    MigrationRisk,
    ResourceSample,
    classify_migration_risk,
    evaluate_resource_window,
    require_transition,
    validate_immutable_artifact,
    validate_migration_plan,
    validate_service_set,
)
from app.services.deployment_service import (
    DeploymentGateError,
    ValidatedExecution,
)


DIGEST = "sha256:" + "a" * 64
SHA = "b" * 40
FINGERPRINT = "c" * 64


def valid_samples() -> tuple[ResourceSample, ...]:
    return tuple(
        ResourceSample(
            elapsed_ms=index * 5000,
            mem_available_bytes=1_200_000_000,
            swap_free_bytes=1_500_000_000,
            root_free_bytes=6_000_000_000,
            docker_free_bytes=6_000_000_000,
        )
        for index in range(7)
    )


class DeploymentPolicyTests(unittest.TestCase):
    def test_migration_risk_classification_and_high_fail_stop(self) -> None:
        self.assertEqual(classify_migration_risk(()), MigrationRisk.NONE)
        self.assertEqual(
            classify_migration_risk(("add_table",)), MigrationRisk.LOW
        )
        self.assertEqual(
            classify_migration_risk(("add_table", "blocking_index")),
            MigrationRisk.MEDIUM,
        )
        self.assertEqual(
            classify_migration_risk(("drop_column",)), MigrationRisk.HIGH
        )
        self.assertEqual(
            classify_migration_risk(("unrecognized_operation",)),
            MigrationRisk.HIGH,
        )
        with self.assertRaises(DeploymentPolicyError):
            validate_migration_plan(
                MigrationRisk.HIGH,
                migration_revision="0004",
                rollback_runbook_ref="docs/rollback.md",
            )
        with self.assertRaises(DeploymentPolicyError):
            validate_migration_plan(
                MigrationRisk.MEDIUM,
                migration_revision="0004",
                rollback_runbook_ref=None,
            )

    def test_resource_gate_exact_window_and_failures(self) -> None:
        self.assertTrue(evaluate_resource_window(valid_samples()).passed)
        cases = (
            (valid_samples()[:-1], "sample_count"),
            (
                tuple(
                    ResourceSample(
                        index * 5000,
                        1_050_000_000 if index < 4 else 1_200_000_000,
                        1_500_000_000,
                        6_000_000_000,
                        6_000_000_000,
                    )
                    for index in range(7)
                ),
                "median_memory",
            ),
            (
                (
                    ResourceSample(
                        0,
                        1_000_000_000,
                        1_500_000_000,
                        6_000_000_000,
                        6_000_000_000,
                    ),
                )
                + valid_samples()[1:],
                "minimum_memory",
            ),
            (
                valid_samples()[:2]
                + (
                    ResourceSample(
                        10000,
                        1_200_000_000,
                        1_300_000_000,
                        6_000_000_000,
                        6_000_000_000,
                    ),
                )
                + valid_samples()[3:],
                "swap",
            ),
        )
        for samples, reason in cases:
            with self.subTest(reason=reason):
                self.assertEqual(
                    evaluate_resource_window(samples).reason, reason
                )

    def test_immutable_artifact_and_service_allowlist(self) -> None:
        validate_immutable_artifact(
            digest=DIGEST,
            image_reference=f"registry.example/maoxx@{DIGEST}",
        )
        for reference in (
            "registry.example/maoxx:latest",
            "registry.example/maoxx:release",
            "registry.example/maoxx@sha256:" + "d" * 64,
        ):
            with self.subTest(reference=reference):
                with self.assertRaises(DeploymentPolicyError):
                    validate_immutable_artifact(
                        digest=DIGEST, image_reference=reference
                    )
        self.assertEqual(
            validate_service_set(("feishu-worker", "api")),
            ("api", "feishu-worker"),
        )
        with self.assertRaises(DeploymentPolicyError):
            validate_service_set(("db",))

    def test_state_machine_rejects_skips_and_replay(self) -> None:
        require_transition(None, "INTENT_CREATED")
        require_transition("PRODUCTION_DEPLOYING", "PRODUCTION_FAILED")
        with self.assertRaises(DeploymentPolicyError):
            require_transition("INTENT_CREATED", "PRODUCTION_DEPLOYING")
        with self.assertRaises(DeploymentPolicyError):
            require_transition("PRODUCTION_HEALTHY", "PRODUCTION_DEPLOYING")

    def test_forged_execution_snapshot_cannot_reach_adapter(
        self,
    ) -> None:
        class Adapter:
            called = False

        class Verifier:
            pass

        class Observer:
            pass

        adapter = Adapter()
        execution = ValidatedExecution(
            deployment_id=uuid4(),
            intent_kind="deploy",
            repository="Mxx1233/Maoxx-OS",
            target_sha=SHA,
            environment="production",
            action_code="production_deploy",
            artifact_digest=DIGEST,
            image_reference=f"registry.example/maoxx@{DIGEST}",
            services=("api",),
            lock_owner="executor-1",
            approval_consumption_id=uuid4(),
        )
        boundary = ControlledExecutionBoundary(adapter, Verifier(), Observer())
        with self.assertRaises(DeploymentGateError) as rejected:
            boundary.deploy(execution)
        self.assertEqual(
            rejected.exception.code, "caller_constructed_execution_forbidden"
        )
        self.assertFalse(adapter.called)

    def test_notification_is_fixed_and_contains_no_execution_claim(
        self,
    ) -> None:
        notification = DeploymentNotification(
            event_type="production_started",
            deployment_id=uuid4(),
            repository="Mxx1233/Maoxx-OS",
            target_sha=SHA,
            artifact_digest=DIGEST,
        )
        text = notification.text()
        self.assertIn(DIGEST, text)
        self.assertNotIn("部署完成", text)
        self.assertNotIn("shell", text.lower())


if __name__ == "__main__":
    unittest.main()
