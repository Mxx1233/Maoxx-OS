import os
import subprocess
import unittest
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.models import User
from app.services.approval_service import (
    ApprovalCommand,
    create_approval_request,
    record_approval_decision,
)
from app.services.deployment_authority import (
    CanonicalHumanIdentity,
    RuntimeObservation,
    RuntimeState,
    VerifiedCiEvidence,
)
from app.services.deployment_executor import ControlledExecutionBoundary
from app.services.deployment_policy import MigrationRisk, ResourceSample
from app.services.deployment_service import (
    DeploymentGateError,
    acquire_deployment_lock,
    create_bound_production_approval,
    create_deployment_intent,
    create_rollback_link,
    record_artifact,
    record_deployment_evidence,
    record_resource_gate,
    record_staging_acceptance,
    release_deployment_lock,
    begin_staging_deployment,
    reconcile_expired_production_lock,
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
        test_env = os.environ | {"DATABASE_URL": cls.test_url}
        for command in (
            ["alembic", "upgrade", "head"],
            ["alembic", "current"],
            ["alembic", "heads"],
            ["alembic", "check"],
        ):
            subprocess.run(
                command, check=True, env=test_env, capture_output=True
            )
        cls.engine = create_engine(cls.test_url)
        cls.sessions = sessionmaker(bind=cls.engine, expire_on_commit=False)
        cls.requester_id = uuid4()
        cls.approver_id = uuid4()
        with cls.sessions() as db:
            db.add_all(
                [
                    User(id=cls.requester_id, display_name="Requester"),
                    User(id=cls.approver_id, display_name="Approver"),
                ]
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

    def _digest(self, char: str = "a") -> str:
        return "sha256:" + char * 64

    def setUp(self) -> None:
        """The lease is the sole mutable operational record; isolate its tests."""
        with self.sessions() as db:
            db.execute(text("DELETE FROM core.deployment_locks"))
            db.commit()

    def _sha(self, char: str = "a") -> str:
        return char * 40

    def _samples(self) -> tuple[ResourceSample, ...]:
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

    class Verifier:
        def __init__(self, *, valid: bool = True) -> None:
            self.valid = valid

        def verify(self, *, repository: str, target_sha: str, run_id: int):
            return VerifiedCiEvidence(
                repository=repository if self.valid else "Other/Repository",
                target_sha=target_sha,
                run_id=run_id,
                workflow_name="CI",
                event="push",
                status="completed",
                conclusion="success",
                quality_gate_conclusion="success",
                protected_main_membership=self.valid,
                verified_at=datetime.now(UTC),
                provenance={"api": "github-checks", "run": str(run_id)},
            )

    class Observer:
        def __init__(self) -> None:
            self.observations: dict[
                tuple[UUID, UUID | None, int], RuntimeObservation
            ] = {}

        def set(
            self,
            *,
            deployment_id: UUID,
            execution_id: UUID | None,
            fencing_token: int,
            state: RuntimeState,
            digest: str | None = None,
            revision: str | None = None,
            health: dict[str, bool] | None = None,
        ) -> None:
            self.observations[(deployment_id, execution_id, fencing_token)] = (
                RuntimeObservation(
                    execution_id=execution_id or uuid4(),
                    deployment_id=deployment_id,
                    fencing_token=fencing_token,
                    state=state,
                    observed_digest=digest,
                    observed_revision=revision,
                    health_checks=health or {},
                    migration_revision="0003_phase_1d_f_deployment",
                    observed_at=datetime.now(UTC),
                    details={},
                )
            )

        def observe(self, *, deployment_id, execution_id, fencing_token):
            return self.observations[
                (deployment_id, execution_id, fencing_token)
            ]

    class Adapter:
        def __init__(self, observer) -> None:
            self.observer = observer
            self.calls: list[UUID] = []
            self.accept_fence = True

        def verify_fencing_token(self, **kwargs) -> bool:
            return self.accept_fence

        def deploy_immutable(self, **kwargs) -> None:
            execution_id = kwargs["execution_id"]
            if execution_id in self.calls:
                raise AssertionError("duplicate mutation")
            self.calls.append(execution_id)
            services = kwargs["services"]
            health = {
                "artifact_digest": True,
                "revision_sha": True,
                "migration": True,
                "smoke": True,
            }
            if "api" in services:
                health.update(
                    {
                        "api.ready": True,
                        "api.http": True,
                        "api.dependency.db": True,
                    }
                )
            if "feishu-worker" in services:
                health.update(
                    {
                        "feishu-worker.ready": True,
                        "feishu-worker.connected": True,
                    }
                )
            self.observer.set(
                deployment_id=kwargs["deployment_id"],
                execution_id=execution_id,
                fencing_token=kwargs["fencing_token"],
                state=RuntimeState.HEALTHY,
                digest=kwargs["artifact_digest"],
                revision=kwargs["target_sha"],
                health=health,
            )

    def _intent(self, *, char: str = "a", kind: str = "deploy"):
        with self.sessions() as db:
            return create_deployment_intent(
                db,
                user_id=self.requester_id,
                intent_kind=kind,
                repository="Mxx1233/Maoxx-OS",
                target_sha=self._sha(char),
                artifact_digest=self._digest(char),
                services=("api",),
                migration_risk=MigrationRisk.NONE,
                migration_revision=None,
                rollback_runbook_ref=None,
                config_fingerprint=self._digest("f"),
                idempotency_key=f"intent-{char}-{uuid4()}",
            )

    def _artifact_and_staging(self, intent, verifier=None) -> None:
        verifier = verifier or self.Verifier()
        with self.sessions() as db:
            record_artifact(
                db,
                deployment_id=intent.id,
                repository=intent.repository,
                target_sha=intent.target_sha,
                digest=intent.artifact_digest,
                image_reference=f"registry.example/maoxx@{intent.artifact_digest}",
                ci_run_id=123,
                ci_verifier=verifier,
                provenance={"builder": "ci"},
                actor_fingerprint=CanonicalHumanIdentity(
                    self.requester_id
                ).fingerprint,
            )
        with self.sessions() as db:
            begin_staging_deployment(
                db,
                deployment_id=intent.id,
                actor_fingerprint=CanonicalHumanIdentity(
                    self.requester_id
                ).fingerprint,
            )
        with self.sessions() as db:
            record_staging_acceptance(
                db,
                deployment_id=intent.id,
                repository=intent.repository,
                target_sha=intent.target_sha,
                artifact_digest=intent.artifact_digest,
                validation_suite_version="phase-1d-f-v1",
                config_fingerprint=intent.config_fingerprint,
                accepted_by_fingerprint=CanonicalHumanIdentity(
                    self.approver_id
                ).fingerprint,
                validity_seconds=3600,
            )

    def _approval_and_lease(self, intent, *, owner="executor"):
        with self.sessions() as db:
            request, _ = create_bound_production_approval(
                db,
                deployment_id=intent.id,
                idempotency_key=f"bound-{uuid4()}",
                ttl_seconds=3600,
            )
        with self.sessions() as db:
            outcome = record_approval_decision(
                db,
                command=ApprovalCommand("approved", request.id),
                feishu_event_id=f"approve-{uuid4()}",
                actor_user_id=self.approver_id,
                actor_open_id="approved-human",
            )
            self.assertEqual(outcome.code, "recorded")
        created = datetime.now(UTC).isoformat()
        with self.sessions() as db:
            record_deployment_evidence(
                db,
                deployment_id=intent.id,
                evidence_type="predeploy_backup",
                evidence_key=f"backup-{uuid4()}",
                status_code="passed",
                payload={
                    "deployment_id": str(intent.id),
                    "archive_id": "backup-test",
                    "sha256": "a" * 64,
                    "nonempty": True,
                    "catalog_validated": True,
                    "restore_verification_ref": "restore-test",
                    "created_at": created,
                    "persistent_state_scope": "postgresql-core",
                },
            )
        with self.sessions() as db:
            record_resource_gate(
                db,
                deployment_id=intent.id,
                evidence_key=f"resource-{uuid4()}",
                samples=self._samples(),
            )
        with self.sessions() as db:
            return acquire_deployment_lock(
                db,
                deployment_id=intent.id,
                owner_identity=owner,
                lease_seconds=300,
                evidence_key=f"lock-{uuid4()}",
            )

    def _prepared(self, *, char="a"):
        intent = self._intent(char=char)
        self._artifact_and_staging(intent)
        lease = self._approval_and_lease(intent)
        with self.sessions() as db:
            start = start_production_deployment(
                db,
                deployment_id=intent.id,
                owner_identity=lease.owner_identity,
                fencing_token=lease.fencing_token,
            )
        self.assertEqual(start.code, "execution_prepared")
        return intent, lease, start

    def test_ci_authority_and_precreated_approval_are_fail_closed(
        self,
    ) -> None:
        intent = self._intent(char="b")
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as ci:
                record_artifact(
                    db,
                    deployment_id=intent.id,
                    repository=intent.repository,
                    target_sha=intent.target_sha,
                    digest=intent.artifact_digest,
                    image_reference=f"registry.example/maoxx@{intent.artifact_digest}",
                    ci_run_id=123,
                    ci_verifier=self.Verifier(valid=False),
                    provenance={},
                    actor_fingerprint=CanonicalHumanIdentity(
                        self.requester_id
                    ).fingerprint,
                )
        self.assertEqual(ci.exception.code, "invalid_ci_evidence")
        self._artifact_and_staging(intent)
        with self.sessions() as db:
            generic = create_approval_request(
                db,
                user_id=self.requester_id,
                action_code="production_deploy",
                repository=intent.repository,
                pull_request_number=None,
                target_sha=intent.target_sha,
                target_environment="production",
                idempotency_key=f"generic-{uuid4()}",
                ttl_seconds=3600,
            )
        from app.services.deployment_service import bind_production_approval

        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as attach:
                bind_production_approval(
                    db,
                    deployment_id=intent.id,
                    approval_request_id=generic.id,
                    requester_fingerprint="f" * 64,
                )
        self.assertEqual(
            attach.exception.code, "precreated_approval_attachment_forbidden"
        )

    def test_fencing_stale_takeover_requires_safe_reconciliation(self) -> None:
        first = self._intent(char="c")
        with self.sessions() as db:
            lease = acquire_deployment_lock(
                db,
                deployment_id=first.id,
                owner_identity="first",
                lease_seconds=5,
                evidence_key=f"lock-{uuid4()}",
            )
        second = self._intent(char="d")
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as bypass:
                acquire_deployment_lock(
                    db,
                    deployment_id=second.id,
                    owner_identity="second",
                    lease_seconds=5,
                    evidence_key=f"lock-{uuid4()}",
                    conflict_domain="other",
                )
        self.assertEqual(
            bypass.exception.code, "canonical_conflict_domain_required"
        )
        with self.sessions() as db:
            db.execute(
                text(
                    "UPDATE core.deployment_locks "
                    "SET acquired_at = clock_timestamp() - interval '3 seconds', "
                    "renewed_at = clock_timestamp() - interval '2 seconds', "
                    "lease_expires_at = clock_timestamp() - interval '1 second'"
                )
            )
            db.commit()
        observer = self.Observer()
        observer.set(
            deployment_id=first.id,
            execution_id=None,
            fencing_token=lease.fencing_token,
            state=RuntimeState.IN_PROGRESS,
        )
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as unreconciled:
                acquire_deployment_lock(
                    db,
                    deployment_id=second.id,
                    owner_identity="second",
                    lease_seconds=5,
                    evidence_key=f"takeover-{uuid4()}",
                )
        self.assertEqual(
            unreconciled.exception.code, "stale_lock_reconciliation_required"
        )
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as active:
                reconcile_expired_production_lock(
                    db, observer=observer, evidence_key=f"reconcile-{uuid4()}"
                )
        self.assertEqual(
            active.exception.code, "stale_owner_runtime_not_reconciled"
        )
        observer.set(
            deployment_id=first.id,
            execution_id=None,
            fencing_token=lease.fencing_token,
            state=RuntimeState.FAILED,
        )
        with self.sessions() as db:
            reconcile_expired_production_lock(
                db, observer=observer, evidence_key=f"reconcile-{uuid4()}"
            )
        with self.sessions() as db:
            new_lease = acquire_deployment_lock(
                db,
                deployment_id=second.id,
                owner_identity="second",
                lease_seconds=60,
                evidence_key=f"takeover-{uuid4()}",
            )
        self.assertGreater(new_lease.fencing_token, lease.fencing_token)

    def test_forged_execution_lost_ack_and_fence_rejection(self) -> None:
        intent, lease, start = self._prepared(char="e")
        observer = self.Observer()
        observer.set(
            deployment_id=intent.id,
            execution_id=start.execution_id,
            fencing_token=lease.fencing_token,
            state=RuntimeState.NOT_STARTED,
        )
        adapter = self.Adapter(observer)
        boundary = ControlledExecutionBoundary(
            adapter, self.Verifier(), observer
        )
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as forged:
                boundary.execute(
                    db,
                    deployment_id=intent.id,
                    execution_id=uuid4(),
                    executor_identity=lease.owner_identity,
                    fencing_token=lease.fencing_token,
                )
        self.assertEqual(
            forged.exception.code, "forged_or_stale_execution_authorization"
        )
        with self.sessions() as db:
            result = boundary.execute(
                db,
                deployment_id=intent.id,
                execution_id=start.execution_id,
                executor_identity=lease.owner_identity,
                fencing_token=lease.fencing_token,
            )
        self.assertEqual(result, "PRODUCTION_HEALTHY")
        self.assertEqual(len(adapter.calls), 1)
        with self.sessions() as db:
            replay = start_production_deployment(
                db,
                deployment_id=intent.id,
                owner_identity=lease.owner_identity,
                fencing_token=lease.fencing_token,
            )
        self.assertEqual(replay.code, "reconciliation_required")
        self.assertEqual(replay.execution_id, start.execution_id)
        adapter.accept_fence = False
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError):
                boundary.execute(
                    db,
                    deployment_id=intent.id,
                    execution_id=start.execution_id,
                    executor_identity=lease.owner_identity,
                    fencing_token=lease.fencing_token,
                )
        self.assertEqual(len(adapter.calls), 1)

    def test_freshness_health_and_rollback_revision_are_fail_closed(
        self,
    ) -> None:
        intent, lease, start = self._prepared(char="f")
        observer = self.Observer()
        observer.set(
            deployment_id=intent.id,
            execution_id=start.execution_id,
            fencing_token=lease.fencing_token,
            state=RuntimeState.NOT_STARTED,
        )
        boundary = ControlledExecutionBoundary(
            self.Adapter(observer), self.Verifier(), observer
        )
        with patch(
            "app.services.deployment_service.RESOURCE_EVIDENCE_MAX_AGE_SECONDS",
            -1,
        ):
            with self.sessions() as db:
                with self.assertRaises(DeploymentGateError) as stale:
                    boundary.execute(
                        db,
                        deployment_id=intent.id,
                        execution_id=start.execution_id,
                        executor_identity=lease.owner_identity,
                        fencing_token=lease.fencing_token,
                    )
        self.assertEqual(stale.exception.code, "stale_resource_gate")
        with self.sessions() as db:
            release_deployment_lock(
                db,
                deployment_id=intent.id,
                owner_identity=lease.owner_identity,
                fencing_token=lease.fencing_token,
                evidence_key=f"release-{uuid4()}",
            )

        backup_intent, backup_lease, backup_start = self._prepared(char="8")
        backup_observer = self.Observer()
        backup_observer.set(
            deployment_id=backup_intent.id,
            execution_id=backup_start.execution_id,
            fencing_token=backup_lease.fencing_token,
            state=RuntimeState.NOT_STARTED,
        )
        backup_boundary = ControlledExecutionBoundary(
            self.Adapter(backup_observer), self.Verifier(), backup_observer
        )
        with patch(
            "app.services.deployment_service.BACKUP_EVIDENCE_MAX_AGE_SECONDS",
            -1,
        ):
            with self.sessions() as db:
                with self.assertRaises(DeploymentGateError) as stale_backup:
                    backup_boundary.execute(
                        db,
                        deployment_id=backup_intent.id,
                        execution_id=backup_start.execution_id,
                        executor_identity=backup_lease.owner_identity,
                        fencing_token=backup_lease.fencing_token,
                    )
        self.assertEqual(stale_backup.exception.code, "stale_predeploy_backup")

        rollback = self._intent(char="9", kind="rollback")
        with self.sessions() as db:
            # A failed deployment link is required; use the existing intent after
            # its failed state would be established by authoritative runtime.
            with self.assertRaises(DeploymentGateError):
                create_rollback_link(
                    db,
                    rollback_deployment_id=rollback.id,
                    failed_deployment_id=intent.id,
                    current_production_sha=self._sha("0"),
                    rollback_target_sha=rollback.target_sha,
                    rollback_artifact_digest=rollback.artifact_digest,
                )

    def test_append_only_audit_tables_reject_update_and_delete(self) -> None:
        intent = self._intent(char="1")
        with self.sessions() as db:
            row = record_deployment_evidence(
                db,
                deployment_id=intent.id,
                evidence_type="failure",
                evidence_key=f"evidence-{uuid4()}",
                status_code="recorded",
                payload={"reason": "test"},
            )
        for statement in (
            "UPDATE core.deployment_evidence SET status_code = 'failed' WHERE id = :id",
            "DELETE FROM core.deployment_evidence WHERE id = :id",
        ):
            with self.sessions() as db:
                with self.assertRaises(DBAPIError):
                    db.execute(text(statement), {"id": row.id})
                    db.commit()
                db.rollback()

    def test_partial_health_schema_cannot_mark_deployment_healthy(
        self,
    ) -> None:
        intent, lease, start = self._prepared(char="7")
        observer = self.Observer()
        observer.set(
            deployment_id=intent.id,
            execution_id=start.execution_id,
            fencing_token=lease.fencing_token,
            state=RuntimeState.NOT_STARTED,
        )

        class PartialHealthAdapter(self.Adapter):
            def deploy_immutable(self, **kwargs) -> None:
                self.calls.append(kwargs["execution_id"])
                self.observer.set(
                    deployment_id=kwargs["deployment_id"],
                    execution_id=kwargs["execution_id"],
                    fencing_token=kwargs["fencing_token"],
                    state=RuntimeState.HEALTHY,
                    digest=kwargs["artifact_digest"],
                    revision=kwargs["target_sha"],
                    health={"api.ready": True},
                )

        boundary = ControlledExecutionBoundary(
            PartialHealthAdapter(observer), self.Verifier(), observer
        )
        with self.sessions() as db:
            result = boundary.execute(
                db,
                deployment_id=intent.id,
                execution_id=start.execution_id,
                executor_identity=lease.owner_identity,
                fencing_token=lease.fencing_token,
            )
        self.assertEqual(result, "PRODUCTION_FAILED")

    def test_rollback_revision_mismatch_rejects_before_adapter(self) -> None:
        failed, failed_lease, failed_start = self._prepared(char="6")
        failed_observer = self.Observer()
        failed_observer.set(
            deployment_id=failed.id,
            execution_id=failed_start.execution_id,
            fencing_token=failed_lease.fencing_token,
            state=RuntimeState.NOT_STARTED,
        )

        class FailingAdapter(self.Adapter):
            def deploy_immutable(self, **kwargs) -> None:
                self.calls.append(kwargs["execution_id"])
                self.observer.set(
                    deployment_id=kwargs["deployment_id"],
                    execution_id=kwargs["execution_id"],
                    fencing_token=kwargs["fencing_token"],
                    state=RuntimeState.FAILED,
                )

        with self.sessions() as db:
            self.assertEqual(
                ControlledExecutionBoundary(
                    FailingAdapter(failed_observer),
                    self.Verifier(),
                    failed_observer,
                ).execute(
                    db,
                    deployment_id=failed.id,
                    execution_id=failed_start.execution_id,
                    executor_identity=failed_lease.owner_identity,
                    fencing_token=failed_lease.fencing_token,
                ),
                "PRODUCTION_FAILED",
            )
        with self.sessions() as db:
            release_deployment_lock(
                db,
                deployment_id=failed.id,
                owner_identity=failed_lease.owner_identity,
                fencing_token=failed_lease.fencing_token,
                evidence_key=f"release-{uuid4()}",
            )

        rollback = self._intent(char="5", kind="rollback")
        self._artifact_and_staging(rollback)
        with self.sessions() as db:
            create_rollback_link(
                db,
                rollback_deployment_id=rollback.id,
                failed_deployment_id=failed.id,
                current_production_sha=failed.target_sha,
                rollback_target_sha=rollback.target_sha,
                rollback_artifact_digest=rollback.artifact_digest,
            )
        rollback_lease = self._approval_and_lease(rollback)
        with self.sessions() as db:
            rollback_start = start_production_deployment(
                db,
                deployment_id=rollback.id,
                owner_identity=rollback_lease.owner_identity,
                fencing_token=rollback_lease.fencing_token,
            )
        observer = self.Observer()
        observer.set(
            deployment_id=rollback.id,
            execution_id=rollback_start.execution_id,
            fencing_token=rollback_lease.fencing_token,
            state=RuntimeState.NOT_STARTED,
            revision=self._sha("0"),
        )
        adapter = self.Adapter(observer)
        with self.sessions() as db:
            with self.assertRaises(DeploymentGateError) as mismatch:
                ControlledExecutionBoundary(
                    adapter, self.Verifier(), observer
                ).execute(
                    db,
                    deployment_id=rollback.id,
                    execution_id=rollback_start.execution_id,
                    executor_identity=rollback_lease.owner_identity,
                    fencing_token=rollback_lease.fencing_token,
                )
        self.assertEqual(
            mismatch.exception.code, "rollback_current_revision_mismatch"
        )
        self.assertEqual(adapter.calls, [])


if __name__ == "__main__":
    unittest.main()
