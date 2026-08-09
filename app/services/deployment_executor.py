"""Production execution entry point.

Application code may identify a persisted attempt only.  The host-local
supervisor owns every Docker mutation, durable journal, observation and health
decision.  The former in-process adapter API is permanently fail closed.
"""

from pathlib import Path
from uuid import UUID

from app.services.deployment_service import DeploymentGateError
from app.services.deployment_authority import GitHubCliProtectedMainCiVerifier
from app.config import settings
from app.services.production_supervisor import (
    DockerComposeSupervisorRuntime,
    ProductionDeploymentSupervisor,
    SQLAlchemyAttemptAuthority,
)


def production_supervisor(session_factory) -> ProductionDeploymentSupervisor:
    return ProductionDeploymentSupervisor(
        authority=SQLAlchemyAttemptAuthority(
            session_factory,
            GitHubCliProtectedMainCiVerifier(
                repository=settings.deployment_github_repository,
                repository_id=settings.deployment_github_repository_id,
                workflow_id=settings.deployment_github_workflow_id,
                workflow_path=settings.deployment_github_workflow_path,
            ),
        ),
        runtime=DockerComposeSupervisorRuntime(
            compose_file=Path("/opt/maoxx-os/docker-compose.yml")
        ),
    )


def execute_from_application(*_args: object, **_kwargs: object) -> None:
    raise DeploymentGateError("host_supervisor_required")


def caller_constructed_execution(_execution: object) -> None:
    raise DeploymentGateError("caller_constructed_execution_forbidden")


def submit_attempt_identifier(execution_attempt_id: UUID) -> UUID:
    """Transport-only identifier for a separately governed supervisor invocation."""
    return execution_attempt_id
