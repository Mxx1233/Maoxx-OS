"""The only production-mutation boundary for Phase 1D-F.

It does not accept a caller-built execution object.  Each invocation reloads
authoritative database state and marks one persisted execution attempt before
the fencing-aware adapter is allowed to mutate a runtime.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.deployment_authority import (
    AuthoritativeRuntimeObserver,
    FencingAwareExecutionAdapter,
    ProtectedMainCiVerifier,
)
from app.services.deployment_service import (
    DeploymentGateError,
    prepare_authoritative_mutation,
    reconcile_authoritative_execution,
)


@dataclass(frozen=True)
class ControlledExecutionBoundary:
    """Database-authoritative, fenced executor boundary."""

    adapter: FencingAwareExecutionAdapter
    ci_verifier: ProtectedMainCiVerifier
    runtime_observer: AuthoritativeRuntimeObserver

    def execute(
        self,
        db: Session,
        *,
        deployment_id: UUID,
        execution_id: UUID,
        executor_identity: str,
        fencing_token: int,
    ) -> str:
        execution = prepare_authoritative_mutation(
            db,
            deployment_id=deployment_id,
            execution_id=execution_id,
            executor_identity=executor_identity,
            fencing_token=fencing_token,
            ci_verifier=self.ci_verifier,
            runtime_observer=self.runtime_observer,
        )
        if not self.adapter.verify_fencing_token(
            deployment_id=execution.deployment_id,
            execution_id=execution.execution_id,
            fencing_token=execution.fencing_token,
        ):
            raise DeploymentGateError("executor_fencing_rejected")
        self.adapter.deploy_immutable(
            deployment_id=execution.deployment_id,
            execution_id=execution.execution_id,
            fencing_token=execution.fencing_token,
            image_reference=execution.image_reference,
            artifact_digest=execution.artifact_digest,
            target_sha=execution.target_sha,
            services=execution.services,
            expected_current_revision=execution.expected_current_revision,
        )
        return reconcile_authoritative_execution(
            db,
            deployment_id=deployment_id,
            execution_id=execution_id,
            executor_identity=executor_identity,
            fencing_token=fencing_token,
            ci_verifier=self.ci_verifier,
            runtime_observer=self.runtime_observer,
            evidence_key=f"reconcile-{execution_id}",
        )

    def deploy(self, *args, **kwargs) -> str:
        """Reject the former caller-constructed execution API."""
        del args, kwargs
        raise DeploymentGateError("caller_constructed_execution_forbidden")
