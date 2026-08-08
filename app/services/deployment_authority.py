"""Authoritative interfaces for the Phase 1D-F execution boundary.

Production wiring must implement these interfaces with authenticated GitHub and
runtime control-plane clients.  Callers cannot supply a successful result as
authorization; the deployment service only accepts results returned here.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Protocol
from uuid import UUID


PRODUCTION_CONFLICT_DOMAIN = "production:global"
CI_EVIDENCE_MAX_AGE_SECONDS = 900
BACKUP_EVIDENCE_MAX_AGE_SECONDS = 900
RESOURCE_EVIDENCE_MAX_AGE_SECONDS = 120


class RuntimeState(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
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
    def verify_fencing_token(
        self,
        *,
        deployment_id: UUID,
        execution_id: UUID,
        fencing_token: int,
    ) -> bool: ...

    def deploy_immutable(
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
