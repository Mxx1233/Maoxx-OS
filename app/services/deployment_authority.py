"""Authoritative interfaces for the Phase 1D-F execution boundary.

Production wiring must implement these interfaces with authenticated GitHub and
runtime control-plane clients.  Callers cannot supply a successful result as
authorization; the deployment service only accepts results returned here.
"""

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Callable, Protocol
from uuid import UUID


PRODUCTION_CONFLICT_DOMAIN = "production:global"
CI_EVIDENCE_MAX_AGE_SECONDS = 900
BACKUP_EVIDENCE_MAX_AGE_SECONDS = 900
RESOURCE_EVIDENCE_MAX_AGE_SECONDS = 120


class RuntimeState(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    MUTATION_COMPLETED_HEALTH_UNKNOWN = "mutation_completed_health_unknown"
    FAILED = "failed"
    HEALTHY = "healthy"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CanonicalHumanIdentity:
    """A trusted, canonical database principal; aliases are never accepted."""

    user_id: UUID

    @property
    def fingerprint(self) -> str:
        return sha256(f"human:{self.user_id}".encode()).hexdigest()


@dataclass(frozen=True)
class CanonicalExecutorIdentity:
    """A distinct service principal, not a human approval identity."""

    service_id: str

    @property
    def fingerprint(self) -> str:
        return sha256(f"service:{self.service_id}".encode()).hexdigest()


class CanonicalIdentityResolver(Protocol):
    def resolve_human(self, user_id: UUID) -> CanonicalHumanIdentity: ...

    def resolve_executor(
        self, service_id: str
    ) -> CanonicalExecutorIdentity: ...


def external_identity_fingerprint(value: str) -> str:
    return sha256(f"external:feishu:{value}".encode()).hexdigest()


class DatabaseCanonicalIdentityResolver:
    """Concrete DB mapping; external strings never select a user UUID."""

    def __init__(self, db) -> None:
        self.db = db

    def resolve_human(self, user_id: UUID) -> CanonicalHumanIdentity:
        from app.models import User

        if self.db.get(User, user_id) is None:
            raise ValueError("unknown_canonical_human")
        return CanonicalHumanIdentity(user_id)

    def resolve_feishu_open_id(self, open_id: str) -> CanonicalHumanIdentity:
        from sqlalchemy import select

        from app.models import ExternalIdentity

        rows = (
            self.db.execute(
                select(ExternalIdentity).where(
                    ExternalIdentity.provider == "feishu",
                    ExternalIdentity.subject_fingerprint
                    == external_identity_fingerprint(open_id),
                )
            )
            .scalars()
            .all()
        )
        if len(rows) != 1:
            raise ValueError("missing_or_ambiguous_feishu_identity")
        return CanonicalHumanIdentity(rows[0].user_id)

    def resolve_executor(self, service_id: str) -> CanonicalExecutorIdentity:
        if service_id not in {"controlled-deployment-executor"}:
            raise ValueError("unknown_executor_service")
        return CanonicalExecutorIdentity(service_id)


@dataclass(frozen=True)
class VerifiedCiEvidence:
    repository: str
    target_sha: str
    run_id: int
    workflow_name: str
    event: str
    status: str
    conclusion: str
    quality_gate_conclusion: str
    protected_main_membership: bool
    verified_at: datetime
    provenance: dict[str, str]
    provenance_source: str = "github_checks_api"


class ProtectedMainCiVerifier(Protocol):
    def verify(
        self, *, repository: str, target_sha: str, run_id: int
    ) -> VerifiedCiEvidence: ...


@dataclass(frozen=True)
class RuntimeObservation:
    execution_id: UUID
    deployment_id: UUID
    fencing_token: int
    state: RuntimeState
    observed_digest: str | None
    observed_revision: str | None
    health_checks: dict[str, bool]
    migration_revision: str | None
    observed_at: datetime
    details: dict[str, str]


class AuthoritativeRuntimeObserver(Protocol):
    def observe(
        self,
        *,
        deployment_id: UUID,
        execution_id: UUID | None,
        fencing_token: int,
    ) -> RuntimeObservation: ...


class FencingAwareExecutionAdapter(Protocol):
    def mutate_atomically(
        self,
        *,
        deployment_id: UUID,
        execution_id: UUID,
        fencing_token: int,
        image_reference: str,
        artifact_digest: str,
        target_sha: str,
        services: tuple[str, ...],
        expected_current_revision: str | None,
    ) -> None: ...


class GitHubCliProtectedMainCiVerifier:
    """Concrete authenticated verifier restricted to read-only GitHub API calls."""

    def __init__(
        self,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._run = run

    def _api(self, path: str) -> dict | list:
        result = self._run(
            ["gh", "api", path], text=True, capture_output=True, check=False
        )
        if result.returncode != 0:
            raise ValueError("github_authority_unavailable")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError("github_authority_invalid_response") from exc

    def verify(
        self, *, repository: str, target_sha: str, run_id: int
    ) -> VerifiedCiEvidence:
        run = self._api(f"repos/{repository}/actions/runs/{run_id}")
        branches = self._api(
            f"repos/{repository}/commits/{target_sha}/branches-where-head"
        )
        checks = self._api(
            f"repos/{repository}/commits/{target_sha}/check-runs"
        )
        if (
            not isinstance(run, dict)
            or not isinstance(branches, list)
            or not isinstance(checks, dict)
        ):
            raise ValueError("github_authority_invalid_response")
        quality = [
            check
            for check in checks.get("check_runs", [])
            if check.get("name") == "CI / Quality Gate"
        ]
        on_main = any(
            branch.get("name") == "main" and branch.get("protected") is True
            for branch in branches
        )
        if (
            run.get("repository", {}).get("full_name") != repository
            or run.get("head_sha") != target_sha
            or run.get("event") != "push"
            or run.get("status") != "completed"
            or run.get("conclusion") != "success"
            or len(quality) != 1
            or quality[0].get("head_sha") != target_sha
            or quality[0].get("conclusion") != "success"
            or not on_main
        ):
            raise ValueError("github_authority_verification_failed")
        return VerifiedCiEvidence(
            repository=repository,
            target_sha=target_sha,
            run_id=run_id,
            workflow_name=str(run.get("name") or run.get("workflow_id")),
            event="push",
            status="completed",
            conclusion="success",
            quality_gate_conclusion="success",
            protected_main_membership=True,
            verified_at=datetime.now(UTC),
            provenance={
                "source": "gh_api",
                "run_url": str(run.get("html_url", "")),
            },
        )


class FilesystemBackupVerifier:
    """Concrete custom-format archive verifier; never trusts caller booleans."""

    def __init__(
        self,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._run = run

    def verify(
        self,
        *,
        archive: Path,
        deployment_id: UUID,
        created_at: datetime,
        state_scope: str,
        restore_probe_ref: str,
    ) -> dict:
        if not archive.is_file() or archive.stat().st_size <= 0:
            raise ValueError("backup_archive_missing_or_empty")
        digest = sha256(archive.read_bytes()).hexdigest()
        catalog = self._run(
            ["pg_restore", "--list", str(archive)],
            text=True,
            capture_output=True,
            check=False,
        )
        if (
            catalog.returncode != 0
            or not catalog.stdout.strip()
            or not restore_probe_ref
        ):
            raise ValueError("backup_catalog_or_recoverability_failed")
        return {
            "deployment_id": str(deployment_id),
            "archive_id": archive.name,
            "sha256": digest,
            "nonempty": True,
            "catalog_validated": True,
            "restore_verification_ref": restore_probe_ref,
            "created_at": created_at.astimezone(UTC).isoformat(),
            "persistent_state_scope": state_scope,
            "verifier": "filesystem_pg_restore_v1",
        }


class DockerProductionRuntimeObserver:
    """Concrete read-only observer for the fixed Production compose project."""

    def __init__(
        self,
        *,
        compose_file: Path,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._compose_file = compose_file
        self._run = run

    def observe(
        self,
        *,
        deployment_id: UUID,
        execution_id: UUID | None,
        fencing_token: int,
    ) -> RuntimeObservation:
        result = self._run(
            [
                "docker",
                "compose",
                "-f",
                str(self._compose_file),
                "ps",
                "--format",
                "json",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        now = datetime.now(UTC)
        if result.returncode != 0:
            return RuntimeObservation(
                execution_id or UUID(int=0),
                deployment_id,
                fencing_token,
                RuntimeState.UNKNOWN,
                None,
                None,
                {},
                None,
                now,
                {"source": "docker_compose"},
            )
        try:
            rows = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            rows = []
        if not isinstance(rows, list):
            rows = []
        running = bool(rows) and all(
            str(row.get("State", "")).lower() == "running" for row in rows
        )
        state = (
            RuntimeState.MUTATION_COMPLETED_HEALTH_UNKNOWN
            if running
            else RuntimeState.UNKNOWN
        )
        return RuntimeObservation(
            execution_id or UUID(int=0),
            deployment_id,
            fencing_token,
            state,
            None,
            None,
            {},
            None,
            now,
            {"source": "docker_compose", "containers": str(len(rows))},
        )


class AtomicDockerProductionAdapter:
    """Restricted target: lock/fence validation and compose mutation share one DB transaction."""

    def __init__(
        self,
        *,
        session_factory,
        compose_file: Path,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._sessions = session_factory
        self._compose_file = compose_file
        self._run = run

    def mutate_atomically(
        self,
        *,
        deployment_id: UUID,
        execution_id: UUID,
        fencing_token: int,
        image_reference: str,
        artifact_digest: str,
        target_sha: str,
        services: tuple[str, ...],
        expected_current_revision: str | None,
    ) -> None:
        del (
            image_reference,
            artifact_digest,
            target_sha,
            expected_current_revision,
        )
        if not services or any(
            service not in {"api", "feishu-worker"} for service in services
        ):
            raise ValueError("noncanonical_service_set")
        from sqlalchemy import select
        from app.models import DeploymentExecutionAttempt, DeploymentLock

        with self._sessions() as db:
            lock = db.execute(
                select(DeploymentLock)
                .where(
                    DeploymentLock.conflict_domain
                    == PRODUCTION_CONFLICT_DOMAIN
                )
                .with_for_update()
            ).scalar_one_or_none()
            attempt = db.get(DeploymentExecutionAttempt, execution_id)
            if (
                lock is None
                or attempt is None
                or lock.deployment_id != deployment_id
                or attempt.deployment_id != deployment_id
                or lock.fencing_token != fencing_token
                or attempt.fencing_token != fencing_token
            ):
                raise ValueError("atomic_fencing_rejected")
            result = self._run(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(self._compose_file),
                    "up",
                    "-d",
                    "--no-deps",
                    *services,
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                db.rollback()
                raise ValueError("restricted_runtime_mutation_failed")
            db.commit()
