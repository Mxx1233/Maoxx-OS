import os
import subprocess
import threading
import time
import unittest
from hashlib import sha256
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.models import (
    ApprovalDecision,
    DeploymentApprovalConsumption,
    DeploymentArtifact,
    DeploymentEvidence,
    DeploymentIntent,
    DeploymentStateEvent,
    User,
)
from app.services.approval_service import (
    ApprovalCommand,
    create_approval_request,
    record_approval_decision,
)
from app.services.deployment_notifications import send_deployment_notification
from app.services.deployment_policy import (
    DeploymentPolicyError,
    MigrationRisk,
    ResourceSample,
)
from app.services.deployment_service import (
    CiEvidence,
    DeploymentGateError,
    ExecutionResult,
    acquire_deployment_lock,
    begin_staging_deployment,
    bind_production_approval,
    complete_production_execution,
    create_deployment_intent,
    create_rollback_link,
    invalidate_staging_acceptance,
    reconcile_interrupted_execution,
    record_artifact,
    record_deployment_evidence,
    record_resource_gate,
    record_staging_acceptance,
    release_deployment_lock,
    renew_deployment_lock,
    start_production_deployment,
)


@unittest.skipUnless(
    os.getenv("RUN_DATABASE_INTEGRATION_TESTS") == "1",
    "set RUN_DATABASE_INTEGRATION_TESTS=1 for isolated PostgreSQL tests",
)
class DeploymentDatabaseIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source_url = make_url(os.environ["DATABASE_URL"])
        cls.database_name = f"maoxx_deploy_test_{uuid4().hex}"
        cls.admin_kwargs = {
            "host": source_url.host,
            "port": source_url.port,
            "user": source_url.username,
            "password": source_url.password,
            "dbname": "postgres",
            "autocommit": True,
        }
        cls.test_url = source_url.set(
            database=cls.database_name
        ).render_as_string(hide_password=False)
        with psycopg.connect(**cls.admin_kwargs) as connection:
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(
                    sql.Identifier(cls.database_name)
                )
            )
        test_env = os.environ.copy()
        test_env["DATABASE_URL"] = cls.test_url
        for command in (
            ["alembic", "upgrade", "head"],
            ["alembic", "current"],
            ["alembic", "heads"],
            ["alembic", "check"],
        ):
            subprocess.run(
                command,
                check=True,
                env=test_env,
                capture_output=True,
                text=True,
            )
        cls.engine = create_engine(cls.test_url)
        cls.sessions = sessionmaker(
            bind=cls.engine,
            expire_on_commit=False,
        )
        cls.requester_user_id = uuid4()
        cls.approver_user_id = uuid4()
        cls.requester_fingerprint = sha256(b"requester").hexdigest()
        cls.approver_fingerprint = sha256(b"approver").hexdigest()
        with cls.sessions() as db:
            db.add_all(
                (
                    User(
                        id=cls.requester_user_id,
                        display_name="Deployment Requester",
                    ),
                    User(
                        id=cls.approver_user_id,
                        display_name="Deployment Approver",
                    ),
                )
            )
            db.commit()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()
        with psycopg.connect(**cls.admin_kwargs) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(cls.database_name)
                )
            )

    def _digest(self, value: str) -> str:
        return "sha256:" + value * 64

    def _samples(self, *, memory: int = 1_200_000_000):
        return tuple(
            ResourceSample(
                elapsed_ms=index * 5000,
                mem_available_bytes=memory,
                swap_free_bytes=1_500_000_000,
                root_free_bytes=6_000_000_000,
                docker_free_bytes=6_000_000_000,
            )
            for index in range(7)
        )

    def _intent(
        self,
        suffix: str,
        *,
        intent_kind: str = "deploy",
        target_sha: str | None = None,
        digest: str | None = None,
        migration_risk: MigrationRisk = MigrationRisk.NONE,
        requester_fingerprint: str | None = None,
    ):
        target_sha = target_sha or (suffix[0] * 40)
        digest = digest or self._digest(suffix[0])
        with self.sessions() as db:
            return create_deployment_intent(
                db,
                user_id=self.requester_user_id,
                intent_kind=intent_kind,
                repository="Mxx1233/Maoxx-OS",
                target_sha=target_sha,
                artifact_digest=digest,
                requester_fingerprint=(
                    requester_fingerprint or self.requester_fingerprint
                ),
                services=("api",),
                migration_risk=migration_risk,
                migration_revision=(
                    "0004_medium"
                    if migration_risk is MigrationRisk.MEDIUM
                    else None
                ),
                rollback_runbook_ref=(
                    "docs/rollback-medium.md"
                    if migration_risk is MigrationRisk.MEDIUM
                    else None
                ),
                config_fingerprint=self._digest("f"),
                idempotency_key=f"deployment-{suffix}-{uuid4()}",
            )

    def _record_artifact(self, intent, *, ci: CiEvidence | None = None):
        ci = ci or CiEvidence(
            run_id=12345,
            event="push",
            status="completed",
            conclusion="success",
            head_sha=intent.target_sha,
            quality_gate_conclusion="success",
            protected_main_verified=True,
        )
        with self.sessions() as db:
            return record_artifact(
                db,
                deployment_id=intent.id,
                repository=intent.repository,
                target_sha=intent.target_sha,
                digest=intent.artifact_digest,
                image_reference=(
                    f"registry.example/maoxx@{intent.artifact_digest}"
                ),
                ci=ci,
                provenance={
                    "builder": "isolated-ci",
                    "source_sha": intent.target_sha,
                },
                actor_fingerprint=self.requester_fingerprint,
            )

    def _accept_staging(self, intent, *, validity_seconds: int = 3600):
        with self.sessions() as db:
            begin_staging_deployment(
                db,
                deployment_id=intent.id,
                actor_fingerprint=self.requester_fingerprint,
            )
        with self.sessions() as db:
            return record_staging_acceptance(
                db,
                deployment_id=intent.id,
                repository=intent.repository,
                target_sha=intent.target_sha,
                artifact_digest=intent.artifact_digest,
                validation_suite_version="phase-1d-f-v1",
                config_fingerprint=intent.config_fingerprint,
                accepted_by_fingerprint=self.approver_fingerprint,
                validity_seconds=validity_seconds,
            )

    def _bind_and_decide(
        self,
        intent,
        *,
        decision_code: str = "approved",
        actor_open_id: str = "approver",
        actor_user_id=None,
        ttl_seconds: int = 3600,
        request_overrides: dict | None = None,
    ):
        fields = {
            "action_code": intent.action_code,
            "repository": intent.repository,
            "pull_request_number": None,
            "target_sha": intent.target_sha,
            "target_environment": "production",
        }
        fields.update(request_overrides or {})
        with self.sessions() as db:
            request = create_approval_request(
                db,
                user_id=self.requester_user_id,
                idempotency_key=f"approval-{uuid4()}",
                ttl_seconds=ttl_seconds,
                **fields,
            )
        with self.sessions() as db:
            binding = bind_production_approval(
                db,
                deployment_id=intent.id,
                approval_request_id=request.id,
                requester_fingerprint=intent.requested_by_identity_fingerprint,
            )
        with self.sessions() as db:
            outcome = record_approval_decision(
                db,
                command=ApprovalCommand(decision_code, request.id),
                feishu_event_id=f"deployment-approval-{uuid4()}",
                actor_user_id=actor_user_id or self.approver_user_id,
                actor_open_id=actor_open_id,
            )
        return request, binding, outcome

    def _record_gates_and_lock(
        self,
        intent,
        *,
        owner: str,
        backup_status: str = "passed",
        memory: int = 1_200_000_000,
    ) -> None:
        with self.sessions() as db:
            record_deployment_evidence(
                db,
                deployment_id=intent.id,
                evidence_type="predeploy_backup",
                evidence_key=f"backup-{uuid4()}",
                status_code=backup_status,
                payload=(
                    {
                        "backup_id": "backup-test",
                        "sha256": "a" * 64,
                        "catalog_validated": True,
                    }
                    if backup_status == "passed"
                    else {"reason": "test_failure"}
                ),
            )
        with self.sessions() as db:
            record_resource_gate(
                db,
                deployment_id=intent.id,
                evidence_key=f"resources-{uuid4()}",
                samples=self._samples(memory=memory),
            )
        with self.sessions() as db:
            acquire_deployment_lock(
                db,
                deployment_id=intent.id,
                owner_identity=owner,
                lease_seconds=300,
                evidence_key=f"lock-{uuid4()}",
            )

    def _ready(self, suffix: str, *, owner: str):
        intent = self._intent(suffix)
        self._record_artifact(intent)
        self._accept_staging(intent)
        self._bind_and_decide(intent)
        self._record_gates_and_lock(intent, owner=owner)
        return intent

    def _release(self, intent, owner: str) -> None:
        with self.sessions() as db:
            release_deployment_lock(
                db,
                deployment_id=intent.id,
                owner_identity=owner,
                evidence_key=f"release-{uuid4()}",
            )

    def test_positive_atomic_consumption_replay_and_health_failure(
        self,
    ) -> None:
        owner = "executor-positive"
        intent = self._ready("a-positive", owner=owner)
        with self.sessions() as db:
            replayed_intent = create_deployment_intent(
                db,
                user_id=intent.user_id,
                intent_kind=intent.intent_kind,
                repository=intent.repository,
                target_sha=intent.target_sha,
                artifact_digest=intent.artifact_digest,
                requester_fingerprint=(
                    intent.requested_by_identity_fingerprint
                ),
                services=tuple(intent.service_set),
                migration_risk=MigrationRisk(intent.migration_risk),
                migration_revision=intent.migration_revision,
                rollback_runbook_ref=intent.rollback_runbook_ref,
                config_fingerprint=intent.config_fingerprint,
                idempotency_key=intent.idempotency_key,
            )
        self.assertEqual(replayed_intent.id, intent.id)
        replayed_artifact = self._record_artifact(intent)
        with self.sessions() as db:
            persisted_artifact = db.execute(
                select(DeploymentArtifact).where(
                    DeploymentArtifact.deployment_id == intent.id
                )
            ).scalar_one()
        self.assertEqual(replayed_artifact.id, persisted_artifact.id)
        with self.sessions() as db:
            started = start_production_deployment(
                db,
                deployment_id=intent.id,
                owner_identity=owner,
            )
        self.assertEqual(started.code, "started")
        self.assertIsNotNone(started.execution)
        with self.sessions() as db:
            replay = start_production_deployment(
                db,
                deployment_id=intent.id,
                owner_identity=owner,
            )
        self.assertEqual(replay.code, "already_started")
        with self.sessions() as db:
            consumption_count = db.scalar(
                select(func.count(DeploymentApprovalConsumption.id)).where(
                    DeploymentApprovalConsumption.deployment_id == intent.id
                )
            )
            events = db.scalars(
                select(DeploymentStateEvent)
                .where(DeploymentStateEvent.deployment_id == intent.id)
                .order_by(DeploymentStateEvent.sequence)
            ).all()
        self.assertEqual(consumption_count, 1)
        self.assertEqual(events[-1].to_state, "PRODUCTION_DEPLOYING")
        self.assertEqual(
            events[-1].event_code,
            "approval_consumed_and_deployment_started",
        )
        with self.sessions() as db:
            final_state = complete_production_execution(
                db,
                execution=started.execution,
                result=ExecutionResult(
                    succeeded=True,
                    observed_digest=intent.artifact_digest,
                    before_revision="0" * 40,
                    after_revision=intent.target_sha,
                    health_checks={"api": False},
                    failure_code="health_failed",
                ),
                evidence_key=f"result-{uuid4()}",
            )
        self.assertEqual(final_state, "PRODUCTION_FAILED")
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as replay_after:
                start_production_deployment(
                    db,
                    deployment_id=intent.id,
                    owner_identity=owner,
                )
        self.assertEqual(replay_after.exception.code, "approval_consumed")
        self._release(intent, owner)

    def test_fail_closed_bindings_ci_staging_approval_and_gates(self) -> None:
        intent = self._intent("b-bindings")
        bad_ci = CiEvidence(
            run_id=12345,
            event="push",
            status="completed",
            conclusion="success",
            head_sha="e" * 40,
            quality_gate_conclusion="success",
            protected_main_verified=True,
        )
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as digest_error:
                record_artifact(
                    db,
                    deployment_id=intent.id,
                    repository=intent.repository,
                    target_sha=intent.target_sha,
                    digest=self._digest("e"),
                    image_reference=(
                        f"registry.example/maoxx@{self._digest('e')}"
                    ),
                    ci=bad_ci,
                    provenance={},
                    actor_fingerprint=self.requester_fingerprint,
                )
        self.assertEqual(
            digest_error.exception.code, "artifact_binding_mismatch"
        )
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as ci_error:
                record_artifact(
                    db,
                    deployment_id=intent.id,
                    repository=intent.repository,
                    target_sha=intent.target_sha,
                    digest=intent.artifact_digest,
                    image_reference=(
                        f"registry.example/maoxx@{intent.artifact_digest}"
                    ),
                    ci=bad_ci,
                    provenance={},
                    actor_fingerprint=self.requester_fingerprint,
                )
        self.assertEqual(ci_error.exception.code, "invalid_ci_evidence")
        with self.sessions() as db:
            with self.assertRaises(DeploymentPolicyError):
                record_artifact(
                    db,
                    deployment_id=intent.id,
                    repository=intent.repository,
                    target_sha=intent.target_sha,
                    digest=intent.artifact_digest,
                    image_reference="registry.example/maoxx:latest",
                    ci=bad_ci,
                    provenance={},
                    actor_fingerprint=self.requester_fingerprint,
                )
        self._record_artifact(intent)
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as missing_staging:
                start_production_deployment(
                    db,
                    deployment_id=intent.id,
                    owner_identity="not-acquired",
                )
        self.assertEqual(
            missing_staging.exception.code, "missing_staging_acceptance"
        )
        self._accept_staging(intent)
        mismatch = self._intent("3-staging-mismatch")
        self._record_artifact(mismatch)
        with self.sessions() as db:
            begin_staging_deployment(
                db,
                deployment_id=mismatch.id,
                actor_fingerprint=self.requester_fingerprint,
            )
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as staging_mismatch:
                record_staging_acceptance(
                    db,
                    deployment_id=mismatch.id,
                    repository=mismatch.repository,
                    target_sha=mismatch.target_sha,
                    artifact_digest=self._digest("4"),
                    validation_suite_version="phase-1d-f-v1",
                    config_fingerprint=mismatch.config_fingerprint,
                    accepted_by_fingerprint=self.approver_fingerprint,
                    validity_seconds=3600,
                )
        self.assertEqual(
            staging_mismatch.exception.code, "staging_binding_mismatch"
        )
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as missing_approval:
                start_production_deployment(
                    db,
                    deployment_id=intent.id,
                    owner_identity="not-acquired",
                )
        self.assertEqual(missing_approval.exception.code, "missing_approval")

        wrong_requests = (
            {"repository": "Other/Repository"},
            {"target_sha": "e" * 40},
            {
                "action_code": "development_plan",
                "target_environment": "development",
            },
        )
        for overrides in wrong_requests:
            with self.subTest(overrides=overrides):
                with self.sessions() as db:
                    request = create_approval_request(
                        db,
                        user_id=self.requester_user_id,
                        action_code=overrides.get(
                            "action_code", intent.action_code
                        ),
                        repository=overrides.get(
                            "repository", intent.repository
                        ),
                        pull_request_number=None,
                        target_sha=overrides.get(
                            "target_sha", intent.target_sha
                        ),
                        target_environment=overrides.get(
                            "target_environment", "production"
                        ),
                        idempotency_key=f"wrong-binding-{uuid4()}",
                        ttl_seconds=3600,
                    )
                with self.sessions() as db:
                    with self.assertRaises(DeploymentGateError) as mismatch:
                        bind_production_approval(
                            db,
                            deployment_id=intent.id,
                            approval_request_id=request.id,
                            requester_fingerprint=self.requester_fingerprint,
                        )
                self.assertEqual(
                    mismatch.exception.code, "approval_binding_mismatch"
                )
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as wrong_deployment:
                bind_production_approval(
                    db,
                    deployment_id=uuid4(),
                    approval_request_id=request.id,
                    requester_fingerprint=self.requester_fingerprint,
                )
        self.assertEqual(wrong_deployment.exception.code, "missing_approval")

        already_decided_intent = self._intent("5-predecided")
        self._record_artifact(already_decided_intent)
        self._accept_staging(already_decided_intent)
        with self.sessions() as db:
            predecided_request = create_approval_request(
                db,
                user_id=self.requester_user_id,
                action_code=already_decided_intent.action_code,
                repository=already_decided_intent.repository,
                pull_request_number=None,
                target_sha=already_decided_intent.target_sha,
                target_environment="production",
                idempotency_key=f"predecided-{uuid4()}",
                ttl_seconds=3600,
            )
        with self.sessions() as db:
            record_approval_decision(
                db,
                command=ApprovalCommand("approved", predecided_request.id),
                feishu_event_id=f"predecided-{uuid4()}",
                actor_user_id=self.approver_user_id,
                actor_open_id="approver",
            )
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as predecided:
                bind_production_approval(
                    db,
                    deployment_id=already_decided_intent.id,
                    approval_request_id=predecided_request.id,
                    requester_fingerprint=self.requester_fingerprint,
                )
        self.assertEqual(
            predecided.exception.code,
            "approval_already_decided_before_binding",
        )

        request, _, rejected = self._bind_and_decide(
            intent, decision_code="rejected"
        )
        self.assertEqual(rejected.decision_code, "rejected")
        self._record_gates_and_lock(intent, owner="executor-rejected")
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as not_approved:
                start_production_deployment(
                    db,
                    deployment_id=intent.id,
                    owner_identity="executor-rejected",
                )
        self.assertEqual(not_approved.exception.code, "approval_not_approved")
        self._release(intent, "executor-rejected")

        stale = self._intent("c-stale")
        self._record_artifact(stale)
        self._accept_staging(stale, validity_seconds=1)
        self._bind_and_decide(stale)
        self._record_gates_and_lock(stale, owner="executor-stale")
        time.sleep(1.1)
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as stale_error:
                start_production_deployment(
                    db,
                    deployment_id=stale.id,
                    owner_identity="executor-stale",
                )
        self.assertEqual(
            stale_error.exception.code, "stale_staging_acceptance"
        )
        self._release(stale, "executor-stale")

        invalidated = self._intent("d-invalidated")
        self._record_artifact(invalidated)
        acceptance = self._accept_staging(invalidated)
        with self.sessions() as db:
            invalidate_staging_acceptance(
                db,
                acceptance_id=acceptance.id,
                reason_code="config_changed",
            )
        self._bind_and_decide(invalidated)
        self._record_gates_and_lock(invalidated, owner="executor-invalidated")
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as invalid_error:
                start_production_deployment(
                    db,
                    deployment_id=invalidated.id,
                    owner_identity="executor-invalidated",
                )
        self.assertEqual(
            invalid_error.exception.code, "invalidated_staging_acceptance"
        )
        self._release(invalidated, "executor-invalidated")

    def test_self_approval_expiry_backup_resource_and_lock_guards(
        self,
    ) -> None:
        self_approved = self._intent("e-self")
        self._record_artifact(self_approved)
        self._accept_staging(self_approved)
        self._bind_and_decide(
            self_approved,
            actor_open_id="requester",
            actor_user_id=self.requester_user_id,
        )
        self._record_gates_and_lock(self_approved, owner="executor-self")
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as self_error:
                start_production_deployment(
                    db,
                    deployment_id=self_approved.id,
                    owner_identity="executor-self",
                )
        self.assertEqual(self_error.exception.code, "self_approval")
        self._release(self_approved, "executor-self")

        expired = self._intent("f-expired")
        self._record_artifact(expired)
        self._accept_staging(expired)
        self._bind_and_decide(expired, ttl_seconds=1)
        self._record_gates_and_lock(expired, owner="executor-expired")
        time.sleep(1.1)
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as expiry_error:
                start_production_deployment(
                    db,
                    deployment_id=expired.id,
                    owner_identity="executor-expired",
                )
        self.assertEqual(expiry_error.exception.code, "approval_expired")
        self._release(expired, "executor-expired")

        backup_failed = self._intent("7-backup")
        self._record_artifact(backup_failed)
        self._accept_staging(backup_failed)
        self._bind_and_decide(backup_failed)
        self._record_gates_and_lock(
            backup_failed,
            owner="executor-backup",
            backup_status="failed",
        )
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as backup_error:
                start_production_deployment(
                    db,
                    deployment_id=backup_failed.id,
                    owner_identity="executor-backup",
                )
        self.assertEqual(
            backup_error.exception.code, "missing_predeploy_backup"
        )
        self._release(backup_failed, "executor-backup")

        resource_failed = self._intent("8-resource")
        self._record_artifact(resource_failed)
        self._accept_staging(resource_failed)
        self._bind_and_decide(resource_failed)
        self._record_gates_and_lock(
            resource_failed,
            owner="executor-resource",
            memory=1_000_000_000,
        )
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as resource_error:
                start_production_deployment(
                    db,
                    deployment_id=resource_failed.id,
                    owner_identity="executor-resource",
                )
        self.assertEqual(
            resource_error.exception.code, "missing_resource_gate"
        )
        self._release(resource_failed, "executor-resource")

        lock_a = self._intent("9-lock-a")
        lock_b = self._intent("0-lock-b", target_sha="0" * 40)
        with self.sessions() as db:
            acquire_deployment_lock(
                db,
                deployment_id=lock_a.id,
                owner_identity="owner-a",
                lease_seconds=300,
                evidence_key=f"lock-a-{uuid4()}",
            )
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as conflict:
                acquire_deployment_lock(
                    db,
                    deployment_id=lock_b.id,
                    owner_identity="owner-b",
                    lease_seconds=300,
                    evidence_key=f"lock-b-{uuid4()}",
                )
        self.assertEqual(conflict.exception.code, "lock_conflict")
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as wrong_owner:
                renew_deployment_lock(
                    db,
                    deployment_id=lock_a.id,
                    owner_identity="owner-b",
                    lease_seconds=300,
                    evidence_key=f"renew-wrong-{uuid4()}",
                )
        self.assertEqual(wrong_owner.exception.code, "lock_not_owned")
        with self.sessions() as db:
            db.execute(
                text(
                    "UPDATE core.deployment_locks "
                    "SET acquired_at = clock_timestamp() - interval '10 seconds', "
                    "renewed_at = clock_timestamp() - interval '10 seconds', "
                    "lease_expires_at = clock_timestamp() - interval '1 second' "
                    "WHERE conflict_domain = 'production'"
                )
            )
            db.commit()
        with self.sessions() as db:
            result = acquire_deployment_lock(
                db,
                deployment_id=lock_b.id,
                owner_identity="owner-b",
                lease_seconds=300,
                evidence_key=f"lock-stale-{uuid4()}",
            )
        self.assertEqual(result, "stale_reconciled")
        self._release(lock_b, "owner-b")

    def test_concurrency_reconciliation_rollback_notifications_and_audit(
        self,
    ) -> None:
        owner = "executor-concurrent"
        intent = self._ready("1-concurrent", owner=owner)
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        executions = []

        def start() -> None:
            with self.sessions() as db:
                barrier.wait()
                outcome = start_production_deployment(
                    db,
                    deployment_id=intent.id,
                    owner_identity=owner,
                )
                outcomes.append(outcome.code)
                if outcome.execution is not None:
                    executions.append(outcome.execution)

        threads = [threading.Thread(target=start) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(sorted(outcomes), ["already_started", "started"])
        execution = executions[0]
        with self.sessions() as db:
            state = reconcile_interrupted_execution(
                db,
                execution=execution,
                observed_result=ExecutionResult(
                    succeeded=False,
                    observed_digest=None,
                    before_revision="0" * 40,
                    after_revision=None,
                    health_checks={"api": False},
                    failure_code="interrupted_unknown_state",
                ),
                evidence_key=f"reconcile-{uuid4()}",
            )
        self.assertEqual(state, "PRODUCTION_FAILED")

        class Sink:
            messages: list[str] = []

            def send(self, message: str, *, idempotency_key: str) -> str:
                self.messages.append(message)
                self.idempotency_key = idempotency_key
                return "feishu-reference-test"

        with self.sessions() as db:
            reference = send_deployment_notification(
                db,
                deployment_id=intent.id,
                event_type="production_failed",
                evidence_key=f"notification-{uuid4()}",
                sink=Sink(),
            )
        self.assertEqual(reference, "feishu-reference-test")
        self._release(intent, owner)

        rollback = self._intent(
            "2-rollback",
            intent_kind="rollback",
            target_sha="2" * 40,
            digest=self._digest("2"),
        )
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as target_mismatch:
                create_rollback_link(
                    db,
                    rollback_deployment_id=rollback.id,
                    failed_deployment_id=intent.id,
                    current_production_sha="1" * 40,
                    rollback_target_sha="3" * 40,
                    rollback_artifact_digest=rollback.artifact_digest,
                )
        self.assertEqual(
            target_mismatch.exception.code, "rollback_target_mismatch"
        )
        with self.sessions() as db:
            create_rollback_link(
                db,
                rollback_deployment_id=rollback.id,
                failed_deployment_id=intent.id,
                current_production_sha="1" * 40,
                rollback_target_sha=rollback.target_sha,
                rollback_artifact_digest=rollback.artifact_digest,
            )
        self._record_artifact(rollback)
        self._accept_staging(rollback)
        with self.sessions() as db:
            original_request = db.execute(
                select(ApprovalDecision.request_id)
                .join(
                    DeploymentApprovalConsumption,
                    DeploymentApprovalConsumption.approval_decision_id
                    == ApprovalDecision.id,
                )
                .where(
                    DeploymentApprovalConsumption.deployment_id == intent.id
                )
            ).scalar_one()
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as independent:
                bind_production_approval(
                    db,
                    deployment_id=rollback.id,
                    approval_request_id=original_request,
                    requester_fingerprint=self.requester_fingerprint,
                )
        self.assertEqual(
            independent.exception.code, "approval_binding_mismatch"
        )
        self._bind_and_decide(rollback)
        self._record_gates_and_lock(rollback, owner="executor-rollback")
        with self.sessions() as db:
            rollback_started = start_production_deployment(
                db,
                deployment_id=rollback.id,
                owner_identity="executor-rollback",
            )
        self.assertEqual(rollback_started.code, "started")
        self.assertEqual(
            rollback_started.execution.expected_current_revision,
            "1" * 40,
        )
        with self.sessions() as db:
            rollback_state = complete_production_execution(
                db,
                execution=rollback_started.execution,
                result=ExecutionResult(
                    succeeded=True,
                    observed_digest=rollback.artifact_digest,
                    before_revision="1" * 40,
                    after_revision=rollback.target_sha,
                    health_checks={"api": True},
                ),
                evidence_key=f"rollback-result-{uuid4()}",
            )
        self.assertEqual(rollback_state, "ROLLED_BACK")
        self._release(rollback, "executor-rollback")

        with self.sessions() as db:
            audit_row = db.execute(
                select(DeploymentEvidence)
                .where(DeploymentEvidence.deployment_id == rollback.id)
                .limit(1)
            ).scalar_one()
            state_row = db.execute(
                select(DeploymentStateEvent)
                .where(DeploymentStateEvent.deployment_id == rollback.id)
                .limit(1)
            ).scalar_one()
            artifact_row = db.execute(
                select(DeploymentArtifact).where(
                    DeploymentArtifact.deployment_id == rollback.id
                )
            ).scalar_one()
            intent_row = db.get(DeploymentIntent, rollback.id)
            probes = (
                (DeploymentEvidence, audit_row.id),
                (DeploymentStateEvent, state_row.id),
                (DeploymentArtifact, artifact_row.id),
                (DeploymentIntent, intent_row.id),
            )
        with self.sessions() as db:
            with self.assertRaises(DBAPIError):
                db.execute(
                    text(
                        "UPDATE core.deployment_evidence "
                        "SET status_code = 'failed' WHERE id = :row_id"
                    ),
                    {"row_id": audit_row.id},
                )
                db.commit()
            db.rollback()
            unchanged_status = db.scalar(
                select(DeploymentEvidence.status_code).where(
                    DeploymentEvidence.id == audit_row.id
                )
            )
        self.assertEqual(unchanged_status, audit_row.status_code)
        for model, row_id in probes:
            with self.subTest(table=model.__tablename__):
                with self.sessions() as db:
                    with self.assertRaises(DBAPIError):
                        db.execute(
                            text(
                                f"DELETE FROM core.{model.__tablename__} "
                                "WHERE id = :row_id"
                            ),
                            {"row_id": row_id},
                        )
                        db.commit()
                    db.rollback()


if __name__ == "__main__":
    unittest.main()
