from dataclasses import dataclass
from typing import Protocol

from app.services.deployment_policy import (
    validate_immutable_artifact,
    validate_service_set,
)
from app.services.deployment_service import (
    ExecutionResult,
    ValidatedExecution,
)


class ExecutionAdapter(Protocol):
    def deploy_immutable(
        self,
        *,
        image_reference: str,
        artifact_digest: str,
        services: tuple[str, ...],
        deployment_id: str,
        target_sha: str,
        environment: str,
        action_code: str,
        expected_current_revision: str | None,
    ) -> ExecutionResult: ...


@dataclass(frozen=True)
class ControlledExecutionBoundary:
    """The only boundary permitted to invoke a Production adapter."""

    adapter: ExecutionAdapter

    def deploy(self, execution: ValidatedExecution) -> ExecutionResult:
        validate_immutable_artifact(
            digest=execution.artifact_digest,
            image_reference=execution.image_reference,
        )
        services = validate_service_set(execution.services)
        return self.adapter.deploy_immutable(
            image_reference=execution.image_reference,
            artifact_digest=execution.artifact_digest,
            services=services,
            deployment_id=str(execution.deployment_id),
            target_sha=execution.target_sha,
            environment=execution.environment,
            action_code=execution.action_code,
            expected_current_revision=execution.expected_current_revision,
        )
