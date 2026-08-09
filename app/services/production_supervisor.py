"""Host-local, fail-closed Phase 1D-F Production Deployment Supervisor.

This module is the sole Production Docker mutation principal.  It exposes no
shell strings: every subprocess argv is assembled from validated immutable
database state and a fixed allowlist.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Callable, Protocol
from uuid import UUID

from sqlalchemy import select

from app.models import (
    ApprovalDecision,
    ApprovalRequest,
    DeploymentApprovalBinding,
    DeploymentApprovalConsumption,
    DeploymentArtifact,
    DeploymentExecutionAttempt,
    DeploymentEvidence,
    DeploymentIntent,
    DeploymentLock,
    StagingAcceptance,
)
from app.services.deployment_authority import (
    PRODUCTION_CONFLICT_DOMAIN,
    RuntimeState,
)
from app.services.deployment_authority import ProtectedMainCiVerifier
from app.services.deployment_policy import (
    DIGEST_PATTERN,
    IMMUTABLE_IMAGE_PATTERN,
    SHA_PATTERN,
    required_health_checks,
)
from app.services.resource_authority import (
    TrustedProductionResourceCollector,
    TrustedResourceResult,
)


SUPERVISOR_IDENTITY = "maoxx-production-deployment-supervisor"
PRODUCTION_ENVIRONMENT = "production"
PRODUCTION_LOCK_PATH = Path("/run/maoxx-os/deployment-supervisor.lock")
PRODUCTION_JOURNAL_ROOT = Path("/var/lib/maoxx-os/deployment-journal")
HEALTH_DEADLINE_SECONDS = 120
HEALTH_POLL_SECONDS = 5
QUIESCENCE_SECONDS = 10


class SupervisorError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class JournalEvent(StrEnum):
    ATTEMPT_ACCEPTED = "ATTEMPT_ACCEPTED"
    MUTATION_SUBMITTED = "MUTATION_SUBMITTED"
    MUTATION_COMPLETED = "MUTATION_COMPLETED"
    MUTATION_FAILED = "MUTATION_FAILED"
    HEALTH_STARTED = "HEALTH_STARTED"
    HEALTH_PASSED = "HEALTH_PASSED"
    HEALTH_FAILED = "HEALTH_FAILED"
    RECONCILIATION_STARTED = "RECONCILIATION_STARTED"
    RECONCILIATION_RESULT = "RECONCILIATION_RESULT"
    DB_DISCONNECTED = "DB_DISCONNECTED"
    ATTEMPT_TERMINAL = "ATTEMPT_TERMINAL"


@dataclass(frozen=True)
class SupervisorAttempt:
    deployment_id: UUID
    execution_attempt_id: UUID
    fencing_epoch: int
    repository: str
    target_sha: str
    intent_hash: str
    service_images: dict[str, str]
    artifact_digest: str
    expected_migration_revision: str | None
    staging_accepted_at: datetime


@dataclass(frozen=True)
class RuntimeSnapshot:
    state: RuntimeState
    deployment_id: UUID | None
    execution_attempt_id: UUID | None
    fencing_epoch: int | None
    intent_hash: str | None
    containers: dict[str, dict[str, object]]
    docker_event_cursor: str
    docker_client_active: bool
    transitional: bool
    observed_at: datetime

    def stable_fingerprint(self) -> str:
        # Only fields that are stable across a quiescence window participate
        # in the fingerprint. Docker's Health.Log mutates on every health
        # probe and would otherwise defeat a 10s takeover proof.
        stable_containers = {
            service: {
                "running": detail.get("State", {}).get("Running"),
                "status": detail.get("State", {}).get("Status"),
                "exit_code": detail.get("State", {}).get("ExitCode"),
                "oom_killed": detail.get("State", {}).get("OOMKilled"),
                "restart_count": detail.get("RestartCount"),
                "health_status": detail.get("State", {})
                .get("Health", {})
                .get("Status"),
                "image": detail.get("Config", {}).get("Image"),
                "labels": {
                    key: value
                    for key, value in detail.get("Config", {})
                    .get("Labels", {})
                    .items()
                    if key.startswith("com.maoxx.")
                },
            }
            for service, detail in self.containers.items()
        }
        canonical = json.dumps(
            {
                "state": self.state.value,
                "deployment_id": str(self.deployment_id),
                "execution_attempt_id": str(self.execution_attempt_id),
                "fencing_epoch": self.fencing_epoch,
                "intent_hash": self.intent_hash,
                "containers": stable_containers,
                "transitional": self.transitional,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class HealthCycle:
    checks: dict[str, bool]
    observed_digest: str
    observed_sha: str
    migration_revision: str | None
    completed_at: datetime
    restart_counts: dict[str, int]


def calculate_intent_hash(
    *,
    deployment_id: UUID,
    execution_attempt_id: UUID,
    fencing_epoch: int,
    repository: str,
    target_sha: str,
    service_images: dict[str, str],
    artifact_digest: str,
    expected_migration_revision: str | None,
) -> str:
    payload = json.dumps(
        {
            "deployment_id": str(deployment_id),
            "execution_attempt_id": str(execution_attempt_id),
            "fencing_epoch": fencing_epoch,
            "repository": repository,
            "target_sha": target_sha,
            "service_images": service_images,
            "artifact_digest": artifact_digest,
            "expected_migration_revision": expected_migration_revision,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode()).hexdigest()


class DurableExecutionJournal:
    """Append-only JSONL journal with file and directory fsync."""

    def __init__(self, root: Path, attempt_id: UUID) -> None:
        self.root = root
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.path = self.root / f"{attempt_id}.jsonl"
        directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def append(
        self,
        event: JournalEvent,
        attempt: SupervisorAttempt,
        outcome: dict[str, object] | None = None,
    ) -> None:
        record = {
            "event": event.value,
            "deployment_id": str(attempt.deployment_id),
            "execution_attempt_id": str(attempt.execution_attempt_id),
            "fencing_epoch": attempt.fencing_epoch,
            "intent_hash": attempt.intent_hash,
            "timestamp": datetime.now(UTC).isoformat(),
            "outcome": outcome or {},
        }
        encoded = (json.dumps(record, sort_keys=True) + "\n").encode()
        fd = os.open(
            self.path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC,
            0o600,
        )
        try:
            os.write(fd, encoded)
            os.fsync(fd)
        finally:
            os.close(fd)

    def load(self, attempt: SupervisorAttempt) -> tuple[dict, ...]:
        if not self.path.exists():
            return ()
        records: list[dict] = []
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if (
                    row.get("deployment_id") != str(attempt.deployment_id)
                    or row.get("execution_attempt_id")
                    != str(attempt.execution_attempt_id)
                    or row.get("fencing_epoch") != attempt.fencing_epoch
                    or row.get("intent_hash") != attempt.intent_hash
                    or row.get("event")
                    not in {item.value for item in JournalEvent}
                ):
                    raise SupervisorError("journal_binding_mismatch")
                datetime.fromisoformat(row["timestamp"])
                records.append(row)
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            ValueError,
        ) as exc:
            raise SupervisorError("journal_corrupt") from exc
        return tuple(records)


class HostExecutionLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> HostExecutionLock:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.fd = os.open(
            self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600
        )
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self.fd)
            self.fd = None
            raise SupervisorError("host_execution_lock_busy") from exc
        return self

    def __exit__(self, *_args: object) -> None:
        assert self.fd is not None
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)
        self.fd = None


class SupervisorRuntime(Protocol):
    def validate_plan(self, attempt: SupervisorAttempt) -> None: ...

    def submit(self, attempt: SupervisorAttempt) -> None: ...

    def observe(self, attempt: SupervisorAttempt) -> RuntimeSnapshot: ...

    def events_between(
        self, first: RuntimeSnapshot, second: RuntimeSnapshot
    ) -> bool: ...

    def verify_health(
        self, attempt: SupervisorAttempt, *, deadline: datetime
    ) -> HealthCycle: ...


class SQLAlchemyAttemptAuthority:
    """Reloads the complete immutable attempt from PostgreSQL."""

    def __init__(
        self, session_factory, ci_verifier: ProtectedMainCiVerifier
    ) -> None:
        self._sessions = session_factory
        self._ci_verifier = ci_verifier

    def load(self, execution_attempt_id: UUID) -> SupervisorAttempt:
        try:
            with self._sessions() as db:
                execution = db.execute(
                    select(DeploymentExecutionAttempt).where(
                        DeploymentExecutionAttempt.id == execution_attempt_id
                    )
                ).scalar_one_or_none()
                if execution is None:
                    raise SupervisorError("unknown_execution_attempt")
                intent = db.get(DeploymentIntent, execution.deployment_id)
                artifact = db.execute(
                    select(DeploymentArtifact).where(
                        DeploymentArtifact.deployment_id
                        == execution.deployment_id
                    )
                ).scalar_one_or_none()
                acceptance = db.execute(
                    select(StagingAcceptance).where(
                        StagingAcceptance.deployment_id
                        == execution.deployment_id
                    )
                ).scalar_one_or_none()
                lock = db.execute(
                    select(DeploymentLock).where(
                        DeploymentLock.conflict_domain
                        == PRODUCTION_CONFLICT_DOMAIN
                    )
                ).scalar_one_or_none()
                binding = db.execute(
                    select(DeploymentApprovalBinding).where(
                        DeploymentApprovalBinding.deployment_id
                        == execution.deployment_id
                    )
                ).scalar_one_or_none()
                consumption = db.execute(
                    select(DeploymentApprovalConsumption).where(
                        DeploymentApprovalConsumption.deployment_id
                        == execution.deployment_id
                    )
                ).scalar_one_or_none()
                request = (
                    db.get(ApprovalRequest, binding.approval_request_id)
                    if binding is not None
                    else None
                )
                decision = (
                    db.execute(
                        select(ApprovalDecision).where(
                            ApprovalDecision.request_id
                            == binding.approval_request_id
                        )
                    ).scalar_one_or_none()
                    if binding is not None
                    else None
                )
                backup = db.execute(
                    select(DeploymentEvidence)
                    .where(
                        DeploymentEvidence.deployment_id
                        == execution.deployment_id,
                        DeploymentEvidence.evidence_type == "predeploy_backup",
                    )
                    .order_by(DeploymentEvidence.occurred_at.desc())
                    .limit(1)
                ).scalar_one_or_none()
                if any(
                    value is None
                    for value in (
                        intent,
                        artifact,
                        acceptance,
                        lock,
                        binding,
                        consumption,
                        request,
                        decision,
                        backup,
                    )
                ):
                    raise SupervisorError("attempt_authority_incomplete")
                service_images = dict(artifact.service_images)
                verified_ci = self._ci_verifier.verify(
                    repository=intent.repository,
                    target_sha=intent.target_sha,
                    run_id=artifact.ci_run_id,
                )
                calculated = calculate_intent_hash(
                    deployment_id=intent.id,
                    execution_attempt_id=execution.id,
                    fencing_epoch=execution.fencing_token,
                    repository=intent.repository,
                    target_sha=intent.target_sha,
                    service_images=service_images,
                    artifact_digest=intent.artifact_digest,
                    expected_migration_revision=intent.migration_revision,
                )
                if (
                    execution.intent_hash != calculated
                    or lock.deployment_id != intent.id
                    or lock.fencing_token != execution.fencing_token
                    or lock.owner_identity != SUPERVISOR_IDENTITY
                    or acceptance.artifact_digest != intent.artifact_digest
                    or acceptance.valid_until <= datetime.now(UTC)
                    or set(service_images) != set(intent.service_set)
                    or request.deployment_id != intent.id
                    or request.artifact_digest != intent.artifact_digest
                    or decision.decision_code != "approved"
                    or consumption.approval_decision_id != decision.id
                    or backup.status_code != "passed"
                    or backup.payload.get("producer_identity")
                    != "maoxx-production-backup-authority"
                    or datetime.fromisoformat(backup.payload["created_at"])
                    < acceptance.completed_at
                    or datetime.fromisoformat(backup.payload["verified_at"])
                    + timedelta(minutes=30)
                    < datetime.now(UTC)
                    or verified_ci.repository != intent.repository
                    or verified_ci.target_sha != intent.target_sha
                    or verified_ci.run_id != artifact.ci_run_id
                    or verified_ci.verified_at + timedelta(minutes=15)
                    < datetime.now(UTC)
                ):
                    raise SupervisorError("attempt_authority_mismatch")
                return SupervisorAttempt(
                    deployment_id=intent.id,
                    execution_attempt_id=execution.id,
                    fencing_epoch=execution.fencing_token,
                    repository=intent.repository,
                    target_sha=intent.target_sha,
                    intent_hash=execution.intent_hash,
                    service_images=service_images,
                    artifact_digest=intent.artifact_digest,
                    expected_migration_revision=intent.migration_revision,
                    staging_accepted_at=acceptance.completed_at,
                )
        except SupervisorError:
            raise
        except Exception as exc:
            raise SupervisorError("db_disconnected") from exc

    def record_resource(self, result: TrustedResourceResult) -> None:
        result.require_fresh(datetime.now(UTC))
        with self._sessions() as db:
            existing = db.execute(
                select(DeploymentEvidence).where(
                    DeploymentEvidence.deployment_id == result.deployment_id,
                    DeploymentEvidence.evidence_type == "resource_gate",
                    DeploymentEvidence.evidence_key
                    == str(result.checkpoint_nonce),
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise SupervisorError("resource_checkpoint_already_used")
            db.add(
                DeploymentEvidence(
                    deployment_id=result.deployment_id,
                    evidence_type="resource_gate",
                    evidence_key=str(result.checkpoint_nonce),
                    status_code="passed" if result.passed else "failed",
                    occurred_at=result.evaluated_at,
                    payload={
                        "resource_check_id": str(result.resource_check_id),
                        "deployment_id": str(result.deployment_id),
                        "checkpoint_nonce": str(result.checkpoint_nonce),
                        "evaluated_at": result.evaluated_at.isoformat(),
                        "median_mem_available_bytes": result.median_mem_available_bytes,
                        "minimum_mem_available_bytes": result.minimum_mem_available_bytes,
                        "minimum_swap_free_bytes": result.minimum_swap_free_bytes,
                        "minimum_root_free_bytes": result.minimum_root_free_bytes,
                        "minimum_docker_free_bytes": result.minimum_docker_free_bytes,
                        "samples": [
                            sample.__dict__
                            | {
                                "resource_check_id": str(
                                    sample.resource_check_id
                                ),
                                "deployment_id": str(sample.deployment_id),
                                "checkpoint_nonce": str(
                                    sample.checkpoint_nonce
                                ),
                                "captured_at": sample.captured_at.isoformat(),
                            }
                            for sample in result.samples
                        ],
                        "producer_identity": SUPERVISOR_IDENTITY,
                    },
                )
            )
            db.commit()

    def record_runtime_result(
        self,
        attempt: SupervisorAttempt,
        state: RuntimeState,
        *,
        source: str,
    ) -> None:
        """Append authoritative supervisor outcome; caller dictionaries cannot enter."""
        with self._sessions() as db:
            db.add(
                DeploymentEvidence(
                    deployment_id=attempt.deployment_id,
                    evidence_type="supervisor_runtime_result",
                    evidence_key=(
                        f"{attempt.execution_attempt_id}:{source}:{state.value}"
                    ),
                    status_code=(
                        "passed"
                        if state == RuntimeState.HEALTHY
                        else "failed"
                        if state == RuntimeState.FAILED
                        else "pending"
                    ),
                    occurred_at=datetime.now(UTC),
                    payload={
                        "deployment_id": str(attempt.deployment_id),
                        "execution_attempt_id": str(
                            attempt.execution_attempt_id
                        ),
                        "fencing_epoch": attempt.fencing_epoch,
                        "intent_hash": attempt.intent_hash,
                        "runtime_state": state.value,
                        "source": source,
                        "producer_identity": SUPERVISOR_IDENTITY,
                    },
                )
            )
            db.commit()

    def record_mutation_submitted(self, attempt: SupervisorAttempt) -> None:
        """Persist the mutation-submitted marker before any Docker mutation.

        This is the durable cross-check for the journal: if the journal file
        is lost after a crash, a fresh supervisor can detect that a mutation
        was already submitted and fail closed instead of mutating twice.
        """
        with self._sessions() as db:
            existing = db.execute(
                select(DeploymentEvidence).where(
                    DeploymentEvidence.deployment_id == attempt.deployment_id,
                    DeploymentEvidence.evidence_type == "mutation_submitted",
                    DeploymentEvidence.evidence_key
                    == str(attempt.execution_attempt_id),
                )
            ).scalar_one_or_none()
            if existing is not None:
                return
            db.add(
                DeploymentEvidence(
                    deployment_id=attempt.deployment_id,
                    evidence_type="mutation_submitted",
                    evidence_key=str(attempt.execution_attempt_id),
                    status_code="recorded",
                    occurred_at=datetime.now(UTC),
                    payload={
                        "deployment_id": str(attempt.deployment_id),
                        "execution_attempt_id": str(
                            attempt.execution_attempt_id
                        ),
                        "fencing_epoch": attempt.fencing_epoch,
                        "intent_hash": attempt.intent_hash,
                        "producer_identity": SUPERVISOR_IDENTITY,
                    },
                )
            )
            db.commit()

    def mutation_submitted(self, attempt: SupervisorAttempt) -> bool:
        """True when a mutation-submitted marker exists for this attempt."""
        with self._sessions() as db:
            row = db.execute(
                select(DeploymentEvidence).where(
                    DeploymentEvidence.deployment_id == attempt.deployment_id,
                    DeploymentEvidence.evidence_type == "mutation_submitted",
                    DeploymentEvidence.evidence_key
                    == str(attempt.execution_attempt_id),
                )
            ).scalar_one_or_none()
            return row is not None


class ProductionDeploymentSupervisor:
    """The only object authorized to enter the Production Docker mutation path."""

    def __init__(
        self,
        *,
        authority: SQLAlchemyAttemptAuthority,
        runtime: SupervisorRuntime,
        journal_root: Path = PRODUCTION_JOURNAL_ROOT,
        lock_path: Path = PRODUCTION_LOCK_PATH,
        sleep: Callable[[float], None] = time.sleep,
        resource_collector: TrustedProductionResourceCollector | None = None,
    ) -> None:
        self._authority = authority
        self._runtime = runtime
        self._journal_root = journal_root
        self._lock_path = lock_path
        self._sleep = sleep
        self._resource_collector = (
            resource_collector or TrustedProductionResourceCollector()
        )

    def execute(self, execution_attempt_id: UUID) -> RuntimeState:
        with HostExecutionLock(self._lock_path):
            attempt = self._authority.load(execution_attempt_id)
            journal = DurableExecutionJournal(
                self._journal_root, attempt.execution_attempt_id
            )
            prior = journal.load(attempt)
            events = [row["event"] for row in prior]
            if JournalEvent.ATTEMPT_TERMINAL.value in events:
                return self._terminal_state(prior)
            if JournalEvent.MUTATION_SUBMITTED.value in events:
                return self._reconcile_locked(attempt, journal)
            if not events and self._authority.mutation_submitted(attempt):
                # Journal file lost after a crash but the database proves a
                # mutation was already submitted: a second mutation is
                # forbidden. Fail closed and require human intervention.
                raise SupervisorError("journal_missing_after_db_submission")
            journal.append(JournalEvent.ATTEMPT_ACCEPTED, attempt)
            self._runtime.validate_plan(attempt)
            resources = self._resource_collector.collect(attempt.deployment_id)
            resources.require_fresh(datetime.now(UTC))
            try:
                self._authority.record_resource(resources)
            except Exception:
                journal.append(JournalEvent.DB_DISCONNECTED, attempt)
                return RuntimeState.UNKNOWN
            try:
                self._authority.record_mutation_submitted(attempt)
            except Exception:
                journal.append(JournalEvent.DB_DISCONNECTED, attempt)
                return RuntimeState.UNKNOWN
            journal.append(JournalEvent.MUTATION_SUBMITTED, attempt)
            try:
                self._runtime.submit(attempt)
            except SupervisorError as exc:
                if exc.code != "docker_mutation_failed":
                    return self._reconcile_locked(attempt, journal)
                journal.append(
                    JournalEvent.MUTATION_FAILED,
                    attempt,
                    {"error_type": type(exc).__name__},
                )
                journal.append(
                    JournalEvent.ATTEMPT_TERMINAL,
                    attempt,
                    {"state": RuntimeState.FAILED.value},
                )
                return self._record_result_or_unknown(
                    attempt, journal, RuntimeState.FAILED, "mutation"
                )
            except Exception:
                return self._reconcile_locked(attempt, journal)
            journal.append(JournalEvent.MUTATION_COMPLETED, attempt)
            return self._health_locked(attempt, journal)

    def reconcile(self, execution_attempt_id: UUID) -> RuntimeState:
        with HostExecutionLock(self._lock_path):
            attempt = self._authority.load(execution_attempt_id)
            journal = DurableExecutionJournal(
                self._journal_root, attempt.execution_attempt_id
            )
            journal.load(attempt)
            return self._reconcile_locked(attempt, journal)

    def prove_quiescent_takeover(self, execution_attempt_id: UUID) -> bool:
        """Lease expiry is irrelevant; only stable authoritative runtime permits takeover."""
        with HostExecutionLock(self._lock_path):
            attempt = self._authority.load(execution_attempt_id)
            journal = DurableExecutionJournal(
                self._journal_root, attempt.execution_attempt_id
            )
            journal.load(attempt)
            journal.append(JournalEvent.RECONCILIATION_STARTED, attempt)
            first = self._runtime.observe(attempt)
            if first.docker_client_active or first.transitional:
                journal.append(
                    JournalEvent.RECONCILIATION_RESULT,
                    attempt,
                    {"state": RuntimeState.UNKNOWN.value},
                )
                return False
            self._sleep(QUIESCENCE_SECONDS)
            second = self._runtime.observe(attempt)
            safe = (
                not second.docker_client_active
                and not second.transitional
                and first.stable_fingerprint() == second.stable_fingerprint()
                and not self._runtime.events_between(first, second)
                and second.state
                in {
                    RuntimeState.HEALTHY,
                    RuntimeState.FAILED,
                    RuntimeState.NOT_STARTED,
                    RuntimeState.MUTATION_COMPLETED_HEALTH_UNKNOWN,
                }
            )
            journal.append(
                JournalEvent.RECONCILIATION_RESULT,
                attempt,
                {
                    "state": second.state.value
                    if safe
                    else RuntimeState.UNKNOWN.value
                },
            )
            return safe

    def _reconcile_locked(
        self, attempt: SupervisorAttempt, journal: DurableExecutionJournal
    ) -> RuntimeState:
        journal.append(JournalEvent.RECONCILIATION_STARTED, attempt)
        snapshot = self._runtime.observe(attempt)
        journal.append(
            JournalEvent.RECONCILIATION_RESULT,
            attempt,
            {"state": snapshot.state.value},
        )
        if snapshot.state == RuntimeState.MUTATION_COMPLETED_HEALTH_UNKNOWN:
            return self._health_locked(attempt, journal)
        if snapshot.state in {RuntimeState.HEALTHY, RuntimeState.FAILED}:
            journal.append(
                JournalEvent.ATTEMPT_TERMINAL,
                attempt,
                {"state": snapshot.state.value},
            )
            return self._record_result_or_unknown(
                attempt, journal, snapshot.state, "reconciliation"
            )
        self._record_result_or_unknown(
            attempt, journal, RuntimeState.UNKNOWN, "reconciliation"
        )
        return RuntimeState.UNKNOWN

    def _health_locked(
        self, attempt: SupervisorAttempt, journal: DurableExecutionJournal
    ) -> RuntimeState:
        journal.append(JournalEvent.HEALTH_STARTED, attempt)
        # The global 120s health window is anchored to the earliest durable
        # evidence of the mutation being complete: prefer the journaled
        # MUTATION_COMPLETED timestamp; when the submit acknowledgement was
        # lost (no MUTATION_COMPLETED record, runtime observed MCHU) fall
        # back to the first HEALTH_STARTED so repeated supervisor restarts
        # cannot extend the window. Journal records were already validated
        # on load (binding + ISO timestamp), so fromisoformat is safe here.
        records = journal.load(attempt)
        anchors = [
            row["timestamp"]
            for row in records
            if row["event"]
            in {
                JournalEvent.MUTATION_COMPLETED.value,
                JournalEvent.HEALTH_STARTED.value,
            }
        ]
        if not anchors:
            journal.append(
                JournalEvent.HEALTH_FAILED,
                attempt,
                {"error_type": "health_window_anchor_missing"},
            )
            journal.append(
                JournalEvent.ATTEMPT_TERMINAL,
                attempt,
                {"state": RuntimeState.FAILED.value},
            )
            return self._record_result_or_unknown(
                attempt, journal, RuntimeState.FAILED, "health"
            )
        completed_at = datetime.fromisoformat(anchors[0])
        deadline = completed_at + timedelta(seconds=HEALTH_DEADLINE_SECONDS)
        try:
            cycle = self._runtime.verify_health(attempt, deadline=deadline)
            required = required_health_checks(
                tuple(sorted(attempt.service_images))
            )
            passed = (
                set(cycle.checks) == required
                and all(cycle.checks.values())
                and cycle.observed_digest == attempt.artifact_digest
                and cycle.observed_sha == attempt.target_sha
                and (
                    attempt.expected_migration_revision is None
                    or cycle.migration_revision
                    == attempt.expected_migration_revision
                )
                and cycle.completed_at <= deadline
            )
        except Exception as exc:
            journal.append(
                JournalEvent.HEALTH_FAILED,
                attempt,
                {"error_type": type(exc).__name__},
            )
            passed = False
        state = RuntimeState.HEALTHY if passed else RuntimeState.FAILED
        journal.append(
            JournalEvent.HEALTH_PASSED
            if passed
            else JournalEvent.HEALTH_FAILED,
            attempt,
            {"state": state.value},
        )
        journal.append(
            JournalEvent.ATTEMPT_TERMINAL,
            attempt,
            {"state": state.value},
        )
        return self._record_result_or_unknown(
            attempt, journal, state, "health"
        )

    def _record_result_or_unknown(
        self,
        attempt: SupervisorAttempt,
        journal: DurableExecutionJournal,
        state: RuntimeState,
        source: str,
    ) -> RuntimeState:
        try:
            self._authority.record_runtime_result(
                attempt, state, source=source
            )
        except Exception:
            journal.append(JournalEvent.DB_DISCONNECTED, attempt)
            return RuntimeState.UNKNOWN
        return state

    @staticmethod
    def _terminal_state(records: tuple[dict, ...]) -> RuntimeState:
        if (
            records
            and records[-1]["event"] == JournalEvent.DB_DISCONNECTED.value
        ):
            return RuntimeState.UNKNOWN
        terminal = [
            row
            for row in records
            if row["event"] == JournalEvent.ATTEMPT_TERMINAL.value
        ]
        if len(terminal) != 1:
            raise SupervisorError("journal_terminal_ambiguous")
        try:
            return RuntimeState(terminal[0]["outcome"]["state"])
        except (KeyError, ValueError) as exc:
            raise SupervisorError("journal_terminal_corrupt") from exc


class DockerComposeSupervisorRuntime:
    """Fixed-argv Production Docker runtime used only by the supervisor."""

    def __init__(
        self,
        *,
        compose_file: Path,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._compose_file = compose_file.resolve()
        self._run = run
        self._sleep = sleep

    def _command(
        self, *args: str, timeout: int = 5
    ) -> subprocess.CompletedProcess[str]:
        return self._run(
            list(args),
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )

    def _override(self, attempt: SupervisorAttempt) -> Path:
        services: dict[str, dict[str, object]] = {}
        labels = {
            "com.maoxx.deployment_id": str(attempt.deployment_id),
            "com.maoxx.execution_attempt_id": str(
                attempt.execution_attempt_id
            ),
            "com.maoxx.target_sha": attempt.target_sha,
            "com.maoxx.image_digest": attempt.artifact_digest,
            "com.maoxx.fencing_epoch": str(attempt.fencing_epoch),
            "com.maoxx.intent_hash": attempt.intent_hash,
        }
        for service, image in attempt.service_images.items():
            services[service] = {
                "image": image,
                "build": None,
                "labels": labels,
            }
        fd, path_text = tempfile.mkstemp(
            prefix="maoxx-supervisor-", suffix=".json"
        )
        try:
            os.write(fd, json.dumps({"services": services}).encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        return Path(path_text)

    def validate_plan(self, attempt: SupervisorAttempt) -> None:
        if (
            not SHA_PATTERN.fullmatch(attempt.target_sha)
            or not DIGEST_PATTERN.fullmatch(attempt.artifact_digest)
            or not attempt.service_images
        ):
            raise SupervisorError("invalid_immutable_attempt")
        for image in attempt.service_images.values():
            if not IMMUTABLE_IMAGE_PATTERN.fullmatch(image):
                raise SupervisorError("mutable_or_invalid_image_reference")
            if image.rsplit("@", 1)[1] != attempt.artifact_digest:
                raise SupervisorError("staging_digest_mismatch")
        override = self._override(attempt)
        try:
            result = self._command(
                "docker",
                "compose",
                "-f",
                str(self._compose_file),
                "-f",
                str(override),
                "config",
                "--format",
                "json",
            )
            if result.returncode != 0:
                raise SupervisorError("compose_resolution_failed")
            plan = json.loads(result.stdout)
            for service, image in attempt.service_images.items():
                resolved = plan.get("services", {}).get(service, {})
                if (
                    resolved.get("image") != image
                    or resolved.get("build") is not None
                ):
                    raise SupervisorError("resolved_plan_not_immutable")
        except json.JSONDecodeError as exc:
            raise SupervisorError("compose_resolution_malformed") from exc
        finally:
            override.unlink(missing_ok=True)

    def submit(self, attempt: SupervisorAttempt) -> None:
        override = self._override(attempt)
        try:
            result = self._command(
                "docker",
                "compose",
                "-f",
                str(self._compose_file),
                "-f",
                str(override),
                "up",
                "-d",
                "--no-build",
                "--no-deps",
                *sorted(attempt.service_images),
                timeout=120,
            )
            if result.returncode != 0:
                raise SupervisorError("docker_mutation_failed")
        finally:
            override.unlink(missing_ok=True)

    def observe(self, attempt: SupervisorAttempt) -> RuntimeSnapshot:
        result = self._command(
            "docker",
            "compose",
            "-f",
            str(self._compose_file),
            "ps",
            "--format",
            "json",
        )
        if result.returncode != 0:
            return RuntimeSnapshot(
                RuntimeState.UNKNOWN,
                None,
                None,
                None,
                None,
                {},
                "",
                True,
                True,
                datetime.now(UTC),
            )
        try:
            rows = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            rows = []
        selected_rows = (
            {
                row.get("Service"): row
                for row in rows
                if row.get("Service") in attempt.service_images
            }
            if isinstance(rows, list)
            else {}
        )
        selected: dict[str, dict[str, object]] = {}
        for service, row in selected_rows.items():
            container_id = str(row.get("ID") or row.get("Name") or "")
            inspected = self._command("docker", "inspect", container_id)
            if inspected.returncode != 0:
                continue
            try:
                detail = json.loads(inspected.stdout)[0]
            except (json.JSONDecodeError, IndexError, TypeError):
                continue
            selected[str(service)] = detail
        transitional = any(
            str(row.get("State", {}).get("Status", "")).lower()
            in {"created", "restarting", "removing", "paused"}
            for row in selected.values()
        )
        running = len(selected) == len(attempt.service_images) and all(
            row.get("State", {}).get("Running") is True
            for row in selected.values()
        )
        labels = [
            row.get("Config", {}).get("Labels", {})
            for row in selected.values()
        ]
        bindings = {
            (
                item.get("com.maoxx.deployment_id"),
                item.get("com.maoxx.execution_attempt_id"),
                item.get("com.maoxx.fencing_epoch"),
                item.get("com.maoxx.intent_hash"),
            )
            for item in labels
        }
        expected_binding = (
            str(attempt.deployment_id),
            str(attempt.execution_attempt_id),
            str(attempt.fencing_epoch),
            attempt.intent_hash,
        )
        binding_valid = bindings == {expected_binding}
        if not selected:
            state = RuntimeState.NOT_STARTED
        elif transitional:
            state = RuntimeState.UNKNOWN
        elif not binding_valid:
            state = RuntimeState.UNKNOWN
        elif not running:
            state = RuntimeState.FAILED
        else:
            state = RuntimeState.MUTATION_COMPLETED_HEALTH_UNKNOWN
        return RuntimeSnapshot(
            state,
            attempt.deployment_id if binding_valid else None,
            attempt.execution_attempt_id if binding_valid else None,
            attempt.fencing_epoch if binding_valid else None,
            attempt.intent_hash if binding_valid else None,
            selected,
            datetime.now(UTC).isoformat(),
            self._docker_client_active(),
            transitional,
            datetime.now(UTC),
        )

    def events_between(
        self, first: RuntimeSnapshot, second: RuntimeSnapshot
    ) -> bool:
        result = self._command(
            "docker",
            "events",
            "--since",
            first.observed_at.isoformat(),
            "--until",
            second.observed_at.isoformat(),
            "--format",
            "{{json .}}",
        )
        return result.returncode != 0 or bool(result.stdout.strip())

    def verify_health(
        self, attempt: SupervisorAttempt, *, deadline: datetime
    ) -> HealthCycle:
        """Poll the full health contract every HEALTH_POLL_SECONDS until the
        deadline; a single complete all-PASS cycle within the window wins.
        On deadline expiry the last real failure is re-raised so the caller
        sees the actual cause, not a generic timeout."""
        last_error: SupervisorError | None = None
        while True:
            try:
                return self._health_cycle_once(attempt, deadline=deadline)
            except SupervisorError as exc:
                if (
                    exc.code != "health_timeout_or_runtime_unknown"
                    or last_error is None
                ):
                    last_error = exc
                if datetime.now(UTC) >= deadline:
                    raise last_error
                self._sleep(HEALTH_POLL_SECONDS)

    def _health_cycle_once(
        self, attempt: SupervisorAttempt, *, deadline: datetime
    ) -> HealthCycle:
        snapshot = self.observe(attempt)
        if (
            datetime.now(UTC) >= deadline
            or snapshot.state != RuntimeState.MUTATION_COMPLETED_HEALTH_UNKNOWN
        ):
            raise SupervisorError("health_timeout_or_runtime_unknown")
        checks = {
            key: False
            for key in required_health_checks(
                tuple(sorted(attempt.service_images))
            )
        }
        checks["artifact_digest"] = True
        checks["revision_sha"] = True
        restart_counts: dict[str, int] = {}
        for service, expected_image in attempt.service_images.items():
            detail = snapshot.containers.get(service, {})
            state = detail.get("State", {})
            labels = detail.get("Config", {}).get("Labels", {})
            image_id = detail.get("Image")
            image_result = self._command(
                "docker", "image", "inspect", expected_image
            )
            try:
                image_detail = json.loads(image_result.stdout)[0]
            except (json.JSONDecodeError, IndexError, TypeError):
                image_detail = {}
            expected_labels = {
                "com.maoxx.deployment_id": str(attempt.deployment_id),
                "com.maoxx.execution_attempt_id": str(
                    attempt.execution_attempt_id
                ),
                "com.maoxx.target_sha": attempt.target_sha,
                "com.maoxx.image_digest": attempt.artifact_digest,
                "com.maoxx.fencing_epoch": str(attempt.fencing_epoch),
                "com.maoxx.intent_hash": attempt.intent_hash,
            }
            restart_counts[service] = int(detail.get("RestartCount", -1))
            immutable_ok = (
                detail.get("Config", {}).get("Image") == expected_image
                and expected_image in image_detail.get("RepoDigests", [])
                and image_id == image_detail.get("Id")
                and all(
                    labels.get(key) == value
                    for key, value in expected_labels.items()
                )
            )
            runtime_ok = (
                state.get("Running") is True
                and state.get("OOMKilled") is False
                and restart_counts[service] == 0
                and str(state.get("Health", {}).get("Status", "healthy"))
                == "healthy"
            )
            checks[f"{service}.alive"] = runtime_ok
            checks[f"{service}.ready"] = runtime_ok
            checks[f"{service}.live"] = runtime_ok
            checks["artifact_digest"] = (
                checks.get("artifact_digest", True) and immutable_ok
            )
            checks["revision_sha"] = (
                checks.get("revision_sha", True)
                and labels.get("com.maoxx.target_sha") == attempt.target_sha
            )
            if service == "api":
                checks["api.http"] = self._probe(
                    "http://127.0.0.1:8000/health", deadline
                )
                checks["api.dependency.db"] = self._probe(
                    "http://127.0.0.1:8000/health/db", deadline
                )
            if service == "feishu-worker":
                worker = self._command(
                    "docker",
                    "compose",
                    "-f",
                    str(self._compose_file),
                    "exec",
                    "-T",
                    "feishu-worker",
                    "python",
                    "-c",
                    "import json,urllib.request; "
                    "print(json.load(urllib.request.urlopen("
                    "'http://127.0.0.1:8081/health', timeout=5)))",
                    timeout=5,
                )
                try:
                    worker_payload = json.loads(worker.stdout)
                except json.JSONDecodeError:
                    worker_payload = {}
                checks["feishu-worker.connected"] = (
                    worker.returncode == 0
                    and worker_payload.get("connected") is True
                )
        migration = self._command(
            "docker",
            "compose",
            "-f",
            str(self._compose_file),
            "exec",
            "-T",
            "api",
            "alembic",
            "current",
            timeout=5,
        )
        actual_migration = (
            migration.stdout.strip().split()[0]
            if migration.returncode == 0 and migration.stdout.strip()
            else None
        )
        checks["migration"] = (
            attempt.expected_migration_revision is None
            or actual_migration == attempt.expected_migration_revision
        )
        checks["smoke"] = all(
            checks.get(name, False)
            for name in checks
            if name.endswith(".ready")
        )
        checks["critical_regression"] = all(
            value == 0 for value in restart_counts.values()
        )
        checks["verification_within_timeout"] = datetime.now(UTC) < deadline
        if not all(checks.values()):
            raise SupervisorError("mandatory_health_check_failed")
        return HealthCycle(
            checks,
            attempt.artifact_digest,
            attempt.target_sha,
            actual_migration,
            datetime.now(UTC),
            restart_counts,
        )

    def _probe(self, url: str, deadline: datetime) -> bool:
        remaining = max(0.0, (deadline - datetime.now(UTC)).total_seconds())
        if remaining <= 0:
            return False
        result = self._command(
            "curl",
            "-fsS",
            "--max-time",
            str(min(5, remaining)),
            url,
            timeout=5,
        )
        return result.returncode == 0

    def _docker_client_active(self) -> bool:
        result = self._command(
            "pgrep", "-af", "docker( compose)? .*maoxx", timeout=5
        )
        return result.returncode == 0 and bool(result.stdout.strip())
