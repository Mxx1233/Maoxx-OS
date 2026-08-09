"""Controlled-deployment orchestration CLI (Phase 1D-F entry points).

Wires the persisted deployment state machine to production operators:
  create-intent -> record-artifact -> begin-staging ->
  record-staging-acceptance -> create-approval -> start-production ->
  execute (supervisor) / status

Every mutation is recorded in PostgreSQL first; this CLI never performs a
Docker mutation by itself.  Execution authority lives in the supervisor.
"""

import argparse
import sys
from uuid import UUID

from app.config import settings
from app.db import SessionLocal
from app.services.deployment_authority import (
    GitHubCliProtectedMainCiVerifier,
)
from app.services.deployment_policy import MigrationRisk
from app.services.deployment_service import (
    DeploymentGateError,
    _latest_state_event,
    begin_staging_deployment,
    create_bound_production_approval,
    create_deployment_intent,
    record_artifact,
    record_staging_acceptance,
    start_production_deployment,
)
from app.services.production_supervisor import (
    DockerComposeSupervisorRuntime,
    ProductionDeploymentSupervisor,
    SQLAlchemyAttemptAuthority,
)


class DeploymentCliError(RuntimeError):
    pass


def _verifier() -> GitHubCliProtectedMainCiVerifier:
    return GitHubCliProtectedMainCiVerifier(
        repository=settings.deployment_github_repository,
        repository_id=settings.deployment_github_repository_id,
        workflow_id=settings.deployment_github_workflow_id,
        workflow_path=settings.deployment_github_workflow_path,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Maoxx OS controlled-deployment orchestration."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    intent = subparsers.add_parser("create-intent")
    intent.add_argument("--tenant-id", required=True)
    intent.add_argument("--open-id", required=True)
    intent.add_argument(
        "--kind", choices=("deploy", "rollback"), default="deploy"
    )
    intent.add_argument("--repository", required=True)
    intent.add_argument("--sha", required=True)
    intent.add_argument("--digest", required=True)
    intent.add_argument("--services", required=True)
    intent.add_argument(
        "--migration-risk",
        choices=tuple(item.value for item in MigrationRisk),
        required=True,
    )
    intent.add_argument("--migration-revision")
    intent.add_argument("--rollback-runbook-ref")
    intent.add_argument("--config-fingerprint", required=True)
    intent.add_argument("--idempotency-key", required=True)

    artifact = subparsers.add_parser("record-artifact")
    artifact.add_argument("--deployment-id", type=UUID, required=True)
    artifact.add_argument("--repository", required=True)
    artifact.add_argument("--sha", required=True)
    artifact.add_argument("--digest", required=True)
    artifact.add_argument("--image-reference", required=True)
    artifact.add_argument("--ci-run-id", type=int, required=True)
    artifact.add_argument("--actor-fingerprint", required=True)

    staging = subparsers.add_parser("begin-staging")
    staging.add_argument("--deployment-id", type=UUID, required=True)
    staging.add_argument("--actor-fingerprint", required=True)

    accept = subparsers.add_parser("record-staging-acceptance")
    accept.add_argument("--deployment-id", type=UUID, required=True)
    accept.add_argument("--repository", required=True)
    accept.add_argument("--sha", required=True)
    accept.add_argument("--digest", required=True)
    accept.add_argument("--validation-suite-version", required=True)
    accept.add_argument("--config-fingerprint", required=True)
    accept.add_argument("--actor-fingerprint", required=True)
    accept.add_argument("--validity-seconds", type=int, default=3600)

    approval = subparsers.add_parser("create-approval")
    approval.add_argument("--deployment-id", type=UUID, required=True)
    approval.add_argument("--idempotency-key", required=True)
    approval.add_argument("--ttl-seconds", type=int, default=1800)

    start = subparsers.add_parser("start-production")
    start.add_argument("--deployment-id", type=UUID, required=True)
    start.add_argument("--owner-identity", required=True)
    start.add_argument("--fencing-token", type=int, required=True)

    execute = subparsers.add_parser("execute")
    execute.add_argument("--execution-attempt-id", type=UUID, required=True)

    status = subparsers.add_parser("status")
    status.add_argument("--deployment-id", type=UUID, required=True)
    return parser


def run(args: argparse.Namespace) -> int:
    if args.command == "create-intent":
        with SessionLocal() as db:
            intent = create_deployment_intent(
                db,
                authenticated_feishu_tenant_id=args.tenant_id,
                authenticated_feishu_open_id=args.open_id,
                intent_kind=args.kind,
                repository=args.repository,
                target_sha=args.sha,
                artifact_digest=args.digest,
                services=tuple(
                    service.strip()
                    for service in args.services.split(",")
                    if service.strip()
                ),
                migration_risk=MigrationRisk(args.migration_risk),
                migration_revision=args.migration_revision,
                rollback_runbook_ref=args.rollback_runbook_ref,
                config_fingerprint=args.config_fingerprint,
                idempotency_key=args.idempotency_key,
            )
        print(f"deployment_id={intent.id}")
        return 0

    if args.command == "record-artifact":
        with SessionLocal() as db:
            artifact = record_artifact(
                db,
                deployment_id=args.deployment_id,
                repository=args.repository,
                target_sha=args.sha,
                digest=args.digest,
                image_reference=args.image_reference,
                ci_run_id=args.ci_run_id,
                ci_verifier=_verifier(),
                provenance={"source": "deployment_cli"},
                actor_fingerprint=args.actor_fingerprint,
            )
        print(f"artifact_digest={artifact.digest}")
        return 0

    if args.command == "begin-staging":
        with SessionLocal() as db:
            begin_staging_deployment(
                db,
                deployment_id=args.deployment_id,
                actor_fingerprint=args.actor_fingerprint,
            )
        print("staging_deploying")
        return 0

    if args.command == "record-staging-acceptance":
        with SessionLocal() as db:
            acceptance = record_staging_acceptance(
                db,
                deployment_id=args.deployment_id,
                repository=args.repository,
                target_sha=args.sha,
                artifact_digest=args.digest,
                validation_suite_version=args.validation_suite_version,
                config_fingerprint=args.config_fingerprint,
                accepted_by_fingerprint=args.actor_fingerprint,
                validity_seconds=args.validity_seconds,
            )
        print(f"staging_acceptance_id={acceptance.id}")
        return 0

    if args.command == "create-approval":
        with SessionLocal() as db:
            request, binding = create_bound_production_approval(
                db,
                deployment_id=args.deployment_id,
                idempotency_key=args.idempotency_key,
                ttl_seconds=args.ttl_seconds,
            )
        print(
            f"approval_request_id={request.id} "
            f"binding_id={binding.id} state=PRODUCTION_APPROVAL_PENDING"
        )
        return 0

    if args.command == "start-production":
        with SessionLocal() as db:
            outcome = start_production_deployment(
                db,
                deployment_id=args.deployment_id,
                owner_identity=args.owner_identity,
                fencing_token=args.fencing_token,
            )
        print(
            f"result={outcome.result} "
            f"execution_attempt_id={outcome.execution_id}"
        )
        return 0

    if args.command == "execute":
        supervisor = ProductionDeploymentSupervisor(
            authority=SQLAlchemyAttemptAuthority(
                SessionLocal,
                _verifier(),
            ),
            runtime=DockerComposeSupervisorRuntime(
                compose_file=__import__("pathlib").Path(
                    "/opt/maoxx-os/docker-compose.yml"
                )
            ),
        )
        state = supervisor.execute(args.execution_attempt_id)
        print(f"runtime_state={state.value}")
        return 0

    if args.command == "status":
        with SessionLocal() as db:
            from app.models import DeploymentIntent

            intent = db.get(DeploymentIntent, args.deployment_id)
            if intent is None:
                raise DeploymentGateError("unknown_deployment")
            latest = _latest_state_event(db, args.deployment_id)
            state = latest.to_state if latest is not None else "none"
        print(
            f"deployment_id={intent.id} sha={intent.target_sha} state={state}"
        )
        return 0

    raise DeploymentCliError(f"unsupported command: {args.command}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        exit_code = run(args)
    except DeploymentGateError as exc:
        print(f"deployment request rejected: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except Exception:
        print("deployment operation failed closed", file=sys.stderr)
        raise SystemExit(2) from None
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
