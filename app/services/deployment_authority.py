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


@dataclass(frozen=True)
class ResolvedExternalHuman:
    identity: CanonicalHumanIdentity
    principal_id: UUID
    mapping_id: UUID
    mapping_version: int


class CanonicalIdentityResolver(Protocol):
    def resolve_human(self, user_id: UUID) -> CanonicalHumanIdentity: ...

    def resolve_executor(
        self, service_id: str
    ) -> CanonicalExecutorIdentity: ...


def external_identity_fingerprint(value: str) -> str:
    return sha256(f"external:{value}".encode()).hexdigest()


class DatabaseCanonicalIdentityResolver:
    """Concrete DB mapping; external strings never select a user UUID."""

    def __init__(self, db) -> None:
        self.db = db

    def resolve_human(self, user_id: UUID) -> CanonicalHumanIdentity:
        from sqlalchemy import select

        from app.models import Principal

        principal = self.db.execute(
            select(Principal).where(
                Principal.user_id == user_id,
                Principal.principal_type == "HUMAN",
            )
        ).scalar_one_or_none()
        if principal is None:
            raise ValueError("unknown_canonical_human")
        return CanonicalHumanIdentity(user_id)

    def resolve_feishu_open_id(
        self, tenant_id: str, open_id: str
    ) -> CanonicalHumanIdentity:
        return self.resolve_feishu_principal(tenant_id, open_id).identity

    def resolve_feishu_principal(
        self, tenant_id: str, open_id: str
    ) -> ResolvedExternalHuman:
        from sqlalchemy import select

        from app.models import ExternalIdentity, Principal

        rows = self.db.execute(
            select(ExternalIdentity, Principal)
            .join(Principal, Principal.id == ExternalIdentity.principal_id)
            .where(
                ExternalIdentity.provider == "feishu",
                ExternalIdentity.tenant_fingerprint
                == external_identity_fingerprint(f"tenant:{tenant_id}"),
                ExternalIdentity.subject_fingerprint
                == external_identity_fingerprint(f"subject:{open_id}"),
                ExternalIdentity.valid_to.is_(None),
                Principal.principal_type == "HUMAN",
            )
        ).all()
        if len(rows) != 1 or rows[0][1].user_id is None:
            raise ValueError("missing_or_ambiguous_feishu_identity")
        mapping, principal = rows[0]
        return ResolvedExternalHuman(
            CanonicalHumanIdentity(principal.user_id),
            principal.id,
            mapping.id,
            mapping.mapping_version,
        )

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


class GitHubCliProtectedMainCiVerifier:
    """Concrete authenticated verifier restricted to read-only GitHub API calls."""

    def __init__(
        self,
        *,
        repository: str,
        repository_id: int,
        workflow_id: int,
        workflow_path: str,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._repository = repository
        self._repository_id = repository_id
        self._workflow_id = workflow_id
        self._workflow_path = workflow_path
        self._run = run

    def _api(self, path: str) -> dict | list:
        result = self._run(
            ["gh", "api", path],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
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
        if repository != self._repository:
            raise ValueError("github_repository_not_configured")
        repo = self._api(f"repos/{repository}")
        workflow = self._api(
            f"repos/{repository}/actions/workflows/{self._workflow_id}"
        )
        run = self._api(f"repos/{repository}/actions/runs/{run_id}")
        comparison = self._api(
            f"repos/{repository}/compare/{target_sha}...main"
        )
        if (
            not isinstance(repo, dict)
            or not isinstance(workflow, dict)
            or not isinstance(run, dict)
            or not isinstance(comparison, dict)
        ):
            raise ValueError("github_authority_invalid_response")
        attempt = run.get("run_attempt")
        if not isinstance(attempt, int) or attempt < 1:
            raise ValueError("github_authority_invalid_attempt")
        jobs = self._api(
            f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
        )
        if not isinstance(jobs, dict):
            raise ValueError("github_authority_invalid_response")
        quality = [
            job
            for job in jobs.get("jobs", [])
            if job.get("name") == "CI / Quality Gate"
        ]
        merge_base = comparison.get("merge_base_commit", {}).get("sha")
        if (
            repo.get("id") != self._repository_id
            or repo.get("full_name") != repository
            or repo.get("default_branch") != "main"
            or workflow.get("id") != self._workflow_id
            or workflow.get("path") != self._workflow_path
            or workflow.get("state") != "active"
            or run.get("repository", {}).get("full_name") != repository
            or run.get("repository", {}).get("id") != self._repository_id
            or run.get("workflow_id") != self._workflow_id
            or run.get("id") != run_id
            or run.get("head_sha") != target_sha
            or run.get("head_branch") != "main"
            or run.get("event") != "push"
            or run.get("status") != "completed"
            or run.get("conclusion") != "success"
            or run.get("actor", {}).get("type") not in {"User", "Bot"}
            or len(quality) != 1
            or quality[0].get("conclusion") != "success"
            or quality[0].get("status") != "completed"
            or merge_base != target_sha
        ):
            raise ValueError("github_authority_verification_failed")
        return VerifiedCiEvidence(
            repository=repository,
            target_sha=target_sha,
            run_id=run_id,
            workflow_name=f"{self._workflow_id}:{self._workflow_path}",
            event="push",
            status="completed",
            conclusion="success",
            quality_gate_conclusion="success",
            protected_main_membership=True,
            verified_at=datetime.now(UTC),
            provenance={
                "source": "gh_api",
                "run_url": str(run.get("html_url", "")),
                "repository_id": str(self._repository_id),
                "run_attempt": str(attempt),
                "quality_gate_job_id": str(quality[0].get("id")),
            },
        )

