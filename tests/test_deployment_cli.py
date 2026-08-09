"""Tests for the controlled-deployment orchestration CLI (H-7 wiring)."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from app.deployment_cli import build_parser, run
from app.services.deployment_policy import MigrationRisk

DEPLOYMENT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
SHA = "b" * 40
DIGEST = "sha256:" + "c" * 64
FINGERPRINT = "d" * 64


class DeploymentCliTests(unittest.TestCase):
    def test_parser_exposes_full_state_machine_commands(self) -> None:
        parser = build_parser()
        commands = sorted(parser._subparsers._group_actions[0].choices.keys())
        self.assertEqual(
            commands,
            [
                "begin-staging",
                "create-approval",
                "create-intent",
                "execute",
                "record-artifact",
                "record-staging-acceptance",
                "start-production",
                "status",
            ],
        )

    def test_create_intent_wires_service_call(self) -> None:
        intent = SimpleNamespace(id=DEPLOYMENT_ID)
        args = SimpleNamespace(
            command="create-intent",
            tenant_id="tenant-a",
            open_id="open-1",
            kind="deploy",
            repository="Mxx1233/Maoxx-OS",
            sha=SHA,
            digest=DIGEST,
            services="api, feishu-worker",
            migration_risk="LOW",
            migration_revision="0004_approval_card_message_id",
            rollback_runbook_ref=None,
            config_fingerprint=FINGERPRINT,
            idempotency_key="intent-1",
        )
        with (
            patch("app.deployment_cli.SessionLocal"),
            patch(
                "app.deployment_cli.create_deployment_intent",
                return_value=intent,
            ) as create,
        ):
            self.assertEqual(run(args), 0)
        create.assert_called_once()
        _, kwargs = create.call_args
        self.assertEqual(kwargs["intent_kind"], "deploy")
        self.assertEqual(kwargs["services"], ("api", "feishu-worker"))
        self.assertEqual(kwargs["migration_risk"], MigrationRisk.LOW)
        self.assertEqual(kwargs["target_sha"], SHA)

    def test_record_artifact_wires_service_call(self) -> None:
        artifact = SimpleNamespace(digest=DIGEST)
        args = SimpleNamespace(
            command="record-artifact",
            deployment_id=DEPLOYMENT_ID,
            repository="Mxx1233/Maoxx-OS",
            sha=SHA,
            digest=DIGEST,
            image_reference=f"registry.example/maoxx@{DIGEST}",
            ci_run_id=123,
            actor_fingerprint=FINGERPRINT,
        )
        with (
            patch("app.deployment_cli.SessionLocal"),
            patch(
                "app.deployment_cli.record_artifact",
                return_value=artifact,
            ) as record,
            patch("app.deployment_cli._verifier") as verifier,
        ):
            self.assertEqual(run(args), 0)
        record.assert_called_once()
        verifier.assert_called_once()

    def test_create_approval_wires_service_call(self) -> None:
        request = SimpleNamespace(
            id=UUID("11111111-1111-4111-8111-111111111111")
        )
        binding = SimpleNamespace(
            id=UUID("22222222-2222-4222-8222-222222222222")
        )
        args = SimpleNamespace(
            command="create-approval",
            deployment_id=DEPLOYMENT_ID,
            idempotency_key="approval-1",
            ttl_seconds=1800,
        )
        with (
            patch("app.deployment_cli.SessionLocal"),
            patch(
                "app.deployment_cli.create_bound_production_approval",
                return_value=(request, binding),
            ) as create,
        ):
            self.assertEqual(run(args), 0)
        create.assert_called_once()
        _, kwargs = create.call_args
        self.assertEqual(kwargs["deployment_id"], DEPLOYMENT_ID)
        self.assertEqual(kwargs["idempotency_key"], "approval-1")

    def test_start_production_wires_service_call(self) -> None:
        outcome = SimpleNamespace(
            result="attempt_created",
            execution_id=UUID("33333333-3333-4333-8333-333333333333"),
        )
        args = SimpleNamespace(
            command="start-production",
            deployment_id=DEPLOYMENT_ID,
            owner_identity="maoxx-production-supervisor",
            fencing_token=7,
        )
        with (
            patch("app.deployment_cli.SessionLocal"),
            patch(
                "app.deployment_cli.start_production_deployment",
                return_value=outcome,
            ) as start,
        ):
            self.assertEqual(run(args), 0)
        start.assert_called_once()
        _, kwargs = start.call_args
        self.assertEqual(kwargs["fencing_token"], 7)
        self.assertEqual(
            kwargs["owner_identity"], "maoxx-production-supervisor"
        )

    def test_unknown_command_fails_closed(self) -> None:
        args = SimpleNamespace(command="nonsense")
        with self.assertRaises(Exception):
            run(args)

    def test_gate_error_prints_rejection(self) -> None:
        from app.services.deployment_service import DeploymentGateError

        args = SimpleNamespace(
            command="status",
            deployment_id=DEPLOYMENT_ID,
        )
        with (
            patch("app.deployment_cli.SessionLocal"),
            patch(
                "app.deployment_cli._latest_state_event",
                side_effect=DeploymentGateError("unknown_deployment"),
            ),
        ):
            with self.assertRaises(DeploymentGateError):
                run(args)


if __name__ == "__main__":
    unittest.main()
