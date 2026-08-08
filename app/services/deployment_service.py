import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    ApprovalDecision,
    ApprovalRequest,
    DeploymentApprovalBinding,
    DeploymentApprovalConsumption,
    DeploymentArtifact,
    DeploymentEvidence,
    DeploymentExecutionAttempt,
    DeploymentIntent,
    DeploymentLock,
    DeploymentRollback,
    DeploymentStateEvent,
    StagingAcceptance,
    StagingAcceptanceInvalidation,
)
from app.services.approval_service import (
    IDEMPOTENCY_KEY_PATTERN,
    REPOSITORY_PATTERN,
)
from app.services.deployment_policy import (
    DIGEST_PATTERN,
    FINGERPRINT_PATTERN,
    SHA_PATTERN,
    DeploymentPolicyError,
    MigrationRisk,
    ResourceSample,
    evaluate_resource_window,
    health_checks_are_complete,
    require_transition,
    validate_immutable_artifact,
    validate_migration_plan,
    validate_service_set,
)
from app.services.deployment_authority import (
    BACKUP_EVIDENCE_MAX_AGE_SECONDS,
    CI_EVIDENCE_MAX_AGE_SECONDS,
    PRODUCTION_CONFLICT_DOMAIN,
    RESOURCE_EVIDENCE_MAX_AGE_SECONDS,
    AuthoritativeRuntimeObserver,
    CanonicalExecutorIdentity,
    CanonicalHumanIdentity,
    DatabaseCanonicalIdentityResolver,
    ProtectedMainCiVerifier,
    RuntimeState,
    VerifiedCiEvidence,
)


OWNER_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
CONFIG_FINGERPRINT_PATTERN = DIGEST_PATTERN
EVIDENCE_KEY_PATTERN = IDEMPOTENCY_KEY_PATTERN
EVIDENCE_TYPES = frozenset(
    {
        "predeploy_backup",
        "resource_gate",
        "lock_acquired",
        "lock_renewed",
        "lock_released",
        "stale_lock_reconciled",
        "executor_started",
        "executor_result",
        "health_verification",
        "interruption_reconciliation",
        "stale_lock_reconciliation",
        "execution_attempt",
        "mutation_started",
        "runtime_observation",
        "feishu_notification",
        "migration_plan",
        "failure",
    }
)


class DeploymentGateError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CiEvidence:
    """Deprecated caller metadata shape; never accepted as authorization."""

    run_id: int
    event: str
    status: str
    conclusion: str
    head_sha: str
    quality_gate_conclusion: str
    protected_main_verified: bool


@dataclass(frozen=True)
class ValidatedExecution:
    """Legacy snapshot; it cannot authorize the executor boundary."""

    deployment_id: UUID
    intent_kind: str
    repository: str
    target_sha: str
    environment: str
    action_code: str
    artifact_digest: str
    image_reference: str
    services: tuple[str, ...]
    lock_owner: str
    approval_consumption_id: UUID
    expected_current_revision: str | None = None


@dataclass(frozen=True)
class StartOutcome:
    code: str
    execution_id: UUID | None = None
    fencing_token: int | None = None


@dataclass(frozen=True)
class ExecutionResult:
    succeeded: bool
    observed_digest: str | None
    before_revision: str
    after_revision: str | None
    health_checks: dict[str, bool]
    failure_code: str | None = None


@dataclass(frozen=True)
class ProductionLease:
    deployment_id: UUID
    owner_identity: str
    fencing_token: int


def _database_now(db: Session):
    return db.execute(select(func.clock_timestamp())).scalar_one()


def _resolve_human_identity(
    db: Session, user_id: UUID
) -> CanonicalHumanIdentity:
    try:
        return DatabaseCanonicalIdentityResolver(db).resolve_human(user_id)
    except ValueError as exc:
        raise DeploymentGateError(
            "unknown_or_ambiguous_human_identity"
        ) from exc


def _resolve_executor_identity(service_id: str) -> CanonicalExecutorIdentity:
    if not OWNER_PATTERN.fullmatch(service_id):
        raise DeploymentGateError("unknown_or_ambiguous_executor_identity")
    return CanonicalExecutorIdentity(service_id)


def _valid_verified_ci(
    evidence: VerifiedCiEvidence,
    repository: str,
    target_sha: str,
    run_id: int,
) -> bool:
    return (
        evidence.provenance_source == "github_checks_api"
        and evidence.repository == repository
        and evidence.target_sha == target_sha
        and evidence.run_id == run_id
        and evidence.event == "push"
        and evidence.status == "completed"
        and evidence.conclusion == "success"
        and evidence.quality_gate_conclusion == "success"
        and evidence.protected_main_membership
        and bool(evidence.workflow_name)
        and bool(evidence.provenance)
        and evidence.verified_at.tzinfo is not None
    )


def _latest_state_event(
    db: Session, deployment_id: UUID
) -> DeploymentStateEvent | None:
    return db.execute(
        select(DeploymentStateEvent)
        .where(DeploymentStateEvent.deployment_id == deployment_id)
        .order_by(DeploymentStateEvent.sequence.desc())
        .limit(1)
    ).scalar_one_or_none()


def _append_state_event(
    db: Session,
    *,
    deployment_id: UUID,
    to_state: str,
    event_code: str,
    actor_fingerprint: str,
    evidence: dict | None = None,
    occurred_at=None,
) -> DeploymentStateEvent:
    if not FINGERPRINT_PATTERN.fullmatch(actor_fingerprint):
        raise DeploymentPolicyError("invalid actor fingerprint")
    latest = _latest_state_event(db, deployment_id)
    from_state = latest.to_state if latest is not None else None
    require_transition(from_state, to_state)
    event = DeploymentStateEvent(
        deployment_id=deployment_id,
        sequence=1 if latest is None else latest.sequence + 1,
        from_state=from_state,
        to_state=to_state,
        event_code=event_code,
        actor_identity_fingerprint=actor_fingerprint,
        evidence=evidence or {},
        occurred_at=occurred_at or _database_now(db),
    )
    db.add(event)
    db.flush()
    return event


def _validate_intent_fields(
    *,
    intent_kind: str,
    action_code: str,
    repository: str,
    target_sha: str,
    artifact_digest: str,
    requester_fingerprint: str,
    services: tuple[str, ...],
    migration_risk: MigrationRisk,
    migration_revision: str | None,
    rollback_runbook_ref: str | None,
    config_fingerprint: str,
    idempotency_key: str,
) -> tuple[str, ...]:
    expected_action = {
        "deploy": "production_deploy",
        "rollback": "rollback_production",
    }.get(intent_kind)
    if expected_action is None or action_code != expected_action:
        raise DeploymentPolicyError("intent/action mismatch")
    if not REPOSITORY_PATTERN.fullmatch(repository) or repository.endswith(
        ".git"
    ):
        raise DeploymentPolicyError("invalid repository")
    if not SHA_PATTERN.fullmatch(target_sha):
        raise DeploymentPolicyError("invalid target SHA")
    if not DIGEST_PATTERN.fullmatch(artifact_digest):
        raise DeploymentPolicyError("invalid artifact digest")
    if not FINGERPRINT_PATTERN.fullmatch(requester_fingerprint):
        raise DeploymentPolicyError("invalid requester fingerprint")
    if not CONFIG_FINGERPRINT_PATTERN.fullmatch(config_fingerprint):
        raise DeploymentPolicyError("invalid config fingerprint")
    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
        raise DeploymentPolicyError("invalid idempotency key")
    validate_migration_plan(
        migration_risk,
        migration_revision=migration_revision,
        rollback_runbook_ref=rollback_runbook_ref,
    )
    return validate_service_set(services)


def create_deployment_intent(
    db: Session,
    *,
    authenticated_feishu_open_id: str,
    intent_kind: str,
    repository: str,
    target_sha: str,
    artifact_digest: str,
    services: tuple[str, ...],
    migration_risk: MigrationRisk,
    migration_revision: str | None,
    rollback_runbook_ref: str | None,
    config_fingerprint: str,
    idempotency_key: str,
) -> DeploymentIntent:
    try:
        requester = DatabaseCanonicalIdentityResolver(
            db
        ).resolve_feishu_open_id(authenticated_feishu_open_id)
    except ValueError as exc:
        raise DeploymentGateError(
            "unknown_or_ambiguous_requester_identity"
        ) from exc
    user_id = requester.user_id
    action_code = {
        "deploy": "production_deploy",
        "rollback": "rollback_production",
    }.get(intent_kind, "")
    normalized_services = _validate_intent_fields(
        intent_kind=intent_kind,
        action_code=action_code,
        repository=repository,
        target_sha=target_sha,
        artifact_digest=artifact_digest,
        requester_fingerprint=requester.fingerprint,
        services=services,
        migration_risk=migration_risk,
        migration_revision=migration_revision,
        rollback_runbook_ref=rollback_runbook_ref,
        config_fingerprint=config_fingerprint,
        idempotency_key=idempotency_key,
    )
    existing = db.execute(
        select(DeploymentIntent).where(
            DeploymentIntent.idempotency_key == idempotency_key
        )
    ).scalar_one_or_none()
    expected = (
        user_id,
        intent_kind,
        repository,
        target_sha,
        artifact_digest,
        requester.fingerprint,
        normalized_services,
        migration_risk.value,
        migration_revision,
        rollback_runbook_ref,
        config_fingerprint,
    )
    if existing is not None:
        actual = (
            existing.user_id,
            existing.intent_kind,
            existing.repository,
            existing.target_sha,
            existing.artifact_digest,
            existing.requested_by_identity_fingerprint,
            tuple(existing.service_set),
            existing.migration_risk,
            existing.migration_revision,
            existing.rollback_runbook_ref,
            existing.config_fingerprint,
        )
        if actual != expected:
            raise DeploymentPolicyError("deployment idempotency conflict")
        return existing

    intent = DeploymentIntent(
        user_id=user_id,
        intent_kind=intent_kind,
        action_code=action_code,
        repository=repository,
        target_sha=target_sha,
        target_environment="production",
        artifact_digest=artifact_digest,
        requested_by_identity_fingerprint=requester.fingerprint,
        service_set=list(normalized_services),
        migration_risk=migration_risk.value,
        migration_revision=migration_revision,
        rollback_runbook_ref=rollback_runbook_ref,
        config_fingerprint=config_fingerprint,
        idempotency_key=idempotency_key,
    )
    db.add(intent)
    db.flush()
    _append_state_event(
        db,
        deployment_id=intent.id,
        to_state="INTENT_CREATED",
        event_code="intent_created",
        actor_fingerprint=requester.fingerprint,
    )
    db.commit()
    db.refresh(intent)
    return intent


def create_rollback_link(
    db: Session,
    *,
    rollback_deployment_id: UUID,
    failed_deployment_id: UUID,
    current_production_sha: str,
    rollback_target_sha: str,
    rollback_artifact_digest: str,
) -> DeploymentRollback:
    rollback_intent = db.execute(
        select(DeploymentIntent)
        .where(DeploymentIntent.id == rollback_deployment_id)
        .with_for_update()
    ).scalar_one_or_none()
    failed_intent = db.get(DeploymentIntent, failed_deployment_id)
    if rollback_intent is None or rollback_intent.intent_kind != "rollback":
        raise DeploymentGateError("invalid_rollback_intent")
    if failed_intent is None:
        raise DeploymentGateError("unknown_failed_deployment")
    latest = _latest_state_event(db, failed_deployment_id)
    if latest is None or latest.to_state != "PRODUCTION_FAILED":
        raise DeploymentGateError("failed_deployment_not_failed")
    if (
        rollback_intent.target_sha != rollback_target_sha
        or rollback_intent.artifact_digest != rollback_artifact_digest
        or not SHA_PATTERN.fullmatch(current_production_sha)
    ):
        raise DeploymentGateError("rollback_target_mismatch")
    link = DeploymentRollback(
        deployment_id=rollback_deployment_id,
        failed_deployment_id=failed_deployment_id,
        current_production_sha=current_production_sha,
        rollback_target_sha=rollback_target_sha,
        rollback_artifact_digest=rollback_artifact_digest,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


def record_artifact(
    db: Session,
    *,
    deployment_id: UUID,
    repository: str,
    target_sha: str,
    digest: str,
    image_reference: str,
    ci_run_id: int,
    ci_verifier: ProtectedMainCiVerifier,
    provenance: dict,
    actor_fingerprint: str,
) -> DeploymentArtifact:
    validate_immutable_artifact(digest=digest, image_reference=image_reference)
    intent = db.execute(
        select(DeploymentIntent)
        .where(DeploymentIntent.id == deployment_id)
        .with_for_update()
    ).scalar_one_or_none()
    if intent is None:
        raise DeploymentGateError("unknown_deployment")
    if (
        repository != intent.repository
        or target_sha != intent.target_sha
        or digest != intent.artifact_digest
    ):
        raise DeploymentGateError("artifact_binding_mismatch")
    verified = ci_verifier.verify(
        repository=repository,
        target_sha=target_sha,
        run_id=ci_run_id,
    )
    if not _valid_verified_ci(verified, repository, target_sha, ci_run_id):
        raise DeploymentGateError("invalid_ci_evidence")
    existing = db.execute(
        select(DeploymentArtifact).where(
            DeploymentArtifact.deployment_id == deployment_id
        )
    ).scalar_one_or_none()
    if existing is not None:
        actual = (
            existing.repository,
            existing.target_sha,
            existing.digest,
            existing.image_reference,
            existing.ci_run_id,
            existing.ci_event,
            existing.ci_status,
            existing.ci_conclusion,
            existing.ci_head_sha,
            existing.quality_gate_conclusion,
            existing.protected_main_verified,
            existing.ci_workflow_name,
            existing.ci_repository,
            existing.ci_provenance_source,
        )
        expected = (
            repository,
            target_sha,
            digest,
            image_reference,
            verified.run_id,
            verified.event,
            verified.status,
            verified.conclusion,
            verified.target_sha,
            verified.quality_gate_conclusion,
            verified.protected_main_membership,
            verified.workflow_name,
            verified.repository,
            verified.provenance_source,
        )
        if actual != expected:
            raise DeploymentGateError("artifact_replay_conflict")
        return existing
    artifact = DeploymentArtifact(
        deployment_id=deployment_id,
        repository=repository,
        target_sha=target_sha,
        digest=digest,
        image_reference=image_reference,
        ci_run_id=verified.run_id,
        ci_event=verified.event,
        ci_status=verified.status,
        ci_conclusion=verified.conclusion,
        ci_head_sha=verified.target_sha,
        quality_gate_conclusion=verified.quality_gate_conclusion,
        protected_main_verified=verified.protected_main_membership,
        ci_workflow_name=verified.workflow_name,
        ci_repository=verified.repository,
        ci_verified_at=verified.verified_at,
        ci_provenance_source=verified.provenance_source,
        provenance={**provenance, "ci_verification": verified.provenance},
    )
    db.add(artifact)
    _append_state_event(
        db,
        deployment_id=deployment_id,
        to_state="ARTIFACT_RECORDED",
        event_code="artifact_recorded",
        actor_fingerprint=actor_fingerprint,
        evidence={"digest": digest, "ci_run_id": verified.run_id},
    )
    db.commit()
    db.refresh(artifact)
    return artifact


def begin_staging_deployment(
    db: Session, *, deployment_id: UUID, actor_fingerprint: str
) -> None:
    intent = db.execute(
        select(DeploymentIntent)
        .where(DeploymentIntent.id == deployment_id)
        .with_for_update()
    ).scalar_one_or_none()
    if intent is None:
        raise DeploymentGateError("unknown_deployment")
    latest = _latest_state_event(db, deployment_id)
    if latest is not None and latest.to_state == "STAGING_DEPLOYING":
        return
    _append_state_event(
        db,
        deployment_id=deployment_id,
        to_state="STAGING_DEPLOYING",
        event_code="staging_deploying",
        actor_fingerprint=actor_fingerprint,
    )
    db.commit()


def record_staging_failure(
    db: Session,
    *,
    deployment_id: UUID,
    actor_fingerprint: str,
    failure_code: str,
) -> None:
    intent = db.execute(
        select(DeploymentIntent)
        .where(DeploymentIntent.id == deployment_id)
        .with_for_update()
    ).scalar_one_or_none()
    if intent is None:
        raise DeploymentGateError("unknown_deployment")
    _append_state_event(
        db,
        deployment_id=deployment_id,
        to_state="STAGING_FAILED",
        event_code="staging_failed",
        actor_fingerprint=actor_fingerprint,
        evidence={"failure_code": failure_code},
    )
    db.commit()


def record_staging_acceptance(
    db: Session,
    *,
    deployment_id: UUID,
    repository: str,
    target_sha: str,
    artifact_digest: str,
    validation_suite_version: str,
    config_fingerprint: str,
    accepted_by_fingerprint: str,
    validity_seconds: int,
) -> StagingAcceptance:
    if validity_seconds < 1 or validity_seconds > 86400:
        raise DeploymentPolicyError("invalid staging acceptance validity")
    if not FINGERPRINT_PATTERN.fullmatch(accepted_by_fingerprint):
        raise DeploymentPolicyError("invalid staging actor fingerprint")
    intent = db.execute(
        select(DeploymentIntent)
        .where(DeploymentIntent.id == deployment_id)
        .with_for_update()
    ).scalar_one_or_none()
    artifact = db.execute(
        select(DeploymentArtifact).where(
            DeploymentArtifact.deployment_id == deployment_id
        )
    ).scalar_one_or_none()
    if intent is None or artifact is None:
        raise DeploymentGateError("missing_artifact")
    if (
        repository != intent.repository
        or target_sha != intent.target_sha
        or artifact_digest != intent.artifact_digest
        or artifact.digest != artifact_digest
        or config_fingerprint != intent.config_fingerprint
    ):
        raise DeploymentGateError("staging_binding_mismatch")
    existing = db.execute(
        select(StagingAcceptance).where(
            StagingAcceptance.deployment_id == deployment_id
        )
    ).scalar_one_or_none()
    if existing is not None:
        actual = (
            existing.repository,
            existing.target_sha,
            existing.artifact_digest,
            existing.validation_suite_version,
            existing.config_fingerprint,
            existing.accepted_by_identity_fingerprint,
        )
        expected = (
            repository,
            target_sha,
            artifact_digest,
            validation_suite_version,
            config_fingerprint,
            accepted_by_fingerprint,
        )
        if actual != expected:
            raise DeploymentGateError("staging_acceptance_replay_conflict")
        return existing
    now = _database_now(db)
    acceptance = StagingAcceptance(
        deployment_id=deployment_id,
        repository=repository,
        target_sha=target_sha,
        artifact_digest=artifact_digest,
        environment="staging",
        validation_suite_version=validation_suite_version,
        config_fingerprint=config_fingerprint,
        accepted_by_identity_fingerprint=accepted_by_fingerprint,
        completed_at=now,
        valid_until=now + timedelta(seconds=validity_seconds),
    )
    db.add(acceptance)
    _append_state_event(
        db,
        deployment_id=deployment_id,
        to_state="STAGING_VALIDATED",
        event_code="staging_accepted",
        actor_fingerprint=accepted_by_fingerprint,
        evidence={
            "artifact_digest": artifact_digest,
            "validation_suite_version": validation_suite_version,
        },
        occurred_at=now,
    )
    db.commit()
    db.refresh(acceptance)
    return acceptance


def invalidate_staging_acceptance(
    db: Session, *, acceptance_id: UUID, reason_code: str
) -> None:
    acceptance = db.get(StagingAcceptance, acceptance_id)
    if acceptance is None:
        raise DeploymentGateError("unknown_staging_acceptance")
    db.add(
        StagingAcceptanceInvalidation(
            acceptance_id=acceptance_id,
            reason_code=reason_code,
        )
    )
    db.commit()


def create_bound_production_approval(
    db: Session,
    *,
    deployment_id: UUID,
    idempotency_key: str,
    ttl_seconds: int,
) -> tuple[ApprovalRequest, DeploymentApprovalBinding]:
    """Create the request and immutable binding in one transaction.

    A generic pre-existing Phase 1D-E request is deliberately not attachable.
    """
    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
        raise DeploymentPolicyError("invalid approval idempotency key")
    if ttl_seconds < 1 or ttl_seconds > 86400:
        raise DeploymentPolicyError("invalid approval ttl")
    intent = db.execute(
        select(DeploymentIntent)
        .where(DeploymentIntent.id == deployment_id)
        .with_for_update()
    ).scalar_one_or_none()
    artifact = db.execute(
        select(DeploymentArtifact).where(
            DeploymentArtifact.deployment_id == deployment_id
        )
    ).scalar_one_or_none()
    if intent is None or artifact is None:
        raise DeploymentGateError("missing_immutable_deployment_intent")
    requester = _resolve_human_identity(db, intent.user_id)
    if requester.fingerprint != intent.requested_by_identity_fingerprint:
        raise DeploymentGateError("canonical_requester_mismatch")
    existing = db.execute(
        select(DeploymentApprovalBinding).where(
            DeploymentApprovalBinding.deployment_id == deployment_id
        )
    ).scalar_one_or_none()
    if existing is not None:
        request = db.execute(
            select(ApprovalRequest)
            .where(ApprovalRequest.id == existing.approval_request_id)
            .with_for_update()
        ).scalar_one()
        if request.idempotency_key != idempotency_key:
            raise DeploymentGateError("approval_binding_replay_conflict")
        return request, existing
    existing_request = db.execute(
        select(ApprovalRequest)
        .where(ApprovalRequest.idempotency_key == idempotency_key)
        .with_for_update()
    ).scalar_one_or_none()
    if existing_request is not None:
        # Even an undecided generic request cannot gain a deployment binding.
        raise DeploymentGateError("precreated_approval_attachment_forbidden")
    now = _database_now(db)
    request = ApprovalRequest(
        user_id=intent.user_id,
        action_code=intent.action_code,
        repository=intent.repository,
        pull_request_number=None,
        target_sha=intent.target_sha,
        target_environment="production",
        deployment_id=intent.id,
        artifact_digest=intent.artifact_digest,
        idempotency_key=idempotency_key,
        requested_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
    )
    db.add(request)
    db.flush()
    binding = DeploymentApprovalBinding(
        deployment_id=deployment_id,
        approval_request_id=request.id,
        repository=intent.repository,
        target_sha=intent.target_sha,
        target_environment=intent.target_environment,
        action_code=intent.action_code,
        artifact_digest=intent.artifact_digest,
        requester_identity_fingerprint=requester.fingerprint,
        requester_user_id=requester.user_id,
    )
    db.add(binding)
    pending_state = (
        "PRODUCTION_APPROVAL_PENDING"
        if intent.intent_kind == "deploy"
        else "ROLLBACK_APPROVAL_PENDING"
    )
    _append_state_event(
        db,
        deployment_id=deployment_id,
        to_state=pending_state,
        event_code="approval_bound",
        actor_fingerprint=requester.fingerprint,
        evidence={
            "approval_request_id": str(request.id),
            "deployment_id": str(intent.id),
            "artifact_digest": intent.artifact_digest,
        },
    )
    db.commit()
    db.refresh(request)
    db.refresh(binding)
    return request, binding


def bind_production_approval(
    db: Session,
    *,
    deployment_id: UUID,
    approval_request_id: UUID,
    requester_fingerprint: str,
) -> DeploymentApprovalBinding:
    """Compatibility guard: retroactive attachment is forbidden in Phase 1D-F."""
    del db, deployment_id, approval_request_id, requester_fingerprint
    raise DeploymentGateError("precreated_approval_attachment_forbidden")


def _add_evidence(
    db: Session,
    *,
    deployment_id: UUID,
    evidence_type: str,
    evidence_key: str,
    status_code: str,
    payload: dict,
    occurred_at=None,
) -> DeploymentEvidence:
    if evidence_type not in EVIDENCE_TYPES:
        raise DeploymentPolicyError("unsupported evidence type")
    if not EVIDENCE_KEY_PATTERN.fullmatch(evidence_key):
        raise DeploymentPolicyError("invalid evidence key")
    if status_code not in {"passed", "failed", "recorded"}:
        raise DeploymentPolicyError("invalid evidence status")
    existing = db.execute(
        select(DeploymentEvidence).where(
            DeploymentEvidence.deployment_id == deployment_id,
            DeploymentEvidence.evidence_type == evidence_type,
            DeploymentEvidence.evidence_key == evidence_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.status_code != status_code or existing.payload != payload:
            raise DeploymentGateError("evidence_replay_conflict")
        return existing
    evidence = DeploymentEvidence(
        deployment_id=deployment_id,
        evidence_type=evidence_type,
        evidence_key=evidence_key,
        status_code=status_code,
        payload=payload,
        occurred_at=occurred_at or _database_now(db),
    )
    db.add(evidence)
    db.flush()
    return evidence


def record_deployment_evidence(
    db: Session,
    *,
    deployment_id: UUID,
    evidence_type: str,
    evidence_key: str,
    status_code: str,
    payload: dict,
) -> DeploymentEvidence:
    if db.get(DeploymentIntent, deployment_id) is None:
        raise DeploymentGateError("unknown_deployment")
    if evidence_type == "resource_gate":
        raise DeploymentGateError("resource_evidence_must_be_evaluated")
    if evidence_type == "predeploy_backup" and status_code == "passed":
        required = {
            "deployment_id",
            "archive_id",
            "sha256",
            "nonempty",
            "catalog_validated",
            "restore_verification_ref",
            "created_at",
            "persistent_state_scope",
            "verifier",
        }
        if (
            set(payload) != required
            or payload["nonempty"] is not True
            or payload["catalog_validated"] is not True
            or not isinstance(payload["archive_id"], str)
            or not payload["archive_id"]
            or not isinstance(payload["restore_verification_ref"], str)
            or not payload["restore_verification_ref"]
            or not isinstance(payload["persistent_state_scope"], str)
            or not payload["persistent_state_scope"]
            or payload["deployment_id"] != str(deployment_id)
            or payload["verifier"] != "filesystem_pg_restore_v1"
        ):
            raise DeploymentGateError("invalid_backup_evidence")
        if not re.fullmatch(r"[0-9a-f]{64}", str(payload["sha256"])):
            raise DeploymentGateError("invalid_backup_checksum")
        try:
            created_at = datetime.fromisoformat(payload["created_at"])
        except (TypeError, ValueError):
            raise DeploymentGateError("invalid_backup_timestamp") from None
        if created_at.tzinfo is None:
            raise DeploymentGateError("invalid_backup_timestamp")
    if evidence_type == "resource_gate" and status_code == "passed":
        required = {
            "deployment_id",
            "checkpoint_id",
            "completed_at",
            "policy_version",
            "sample_count",
            "reason",
            "median_mem_available_bytes",
            "minimum_mem_available_bytes",
            "samples",
        }
        if (
            set(payload) != required
            or payload["deployment_id"] != str(deployment_id)
            or not isinstance(payload["checkpoint_id"], str)
            or not payload["checkpoint_id"]
            or not isinstance(payload["samples"], list)
            or payload["sample_count"] != 7
        ):
            raise DeploymentGateError("invalid_resource_gate_evidence")
        try:
            completed_at = datetime.fromisoformat(payload["completed_at"])
        except (TypeError, ValueError):
            raise DeploymentGateError(
                "invalid_resource_gate_timestamp"
            ) from None
        if completed_at.tzinfo is None:
            raise DeploymentGateError("invalid_resource_gate_timestamp")
    evidence = _add_evidence(
        db,
        deployment_id=deployment_id,
        evidence_type=evidence_type,
        evidence_key=evidence_key,
        status_code=status_code,
        payload=payload,
    )
    db.commit()
    db.refresh(evidence)
    return evidence


def record_resource_gate(
    db: Session,
    *,
    deployment_id: UUID,
    evidence_key: str,
    samples: tuple[ResourceSample, ...],
) -> DeploymentEvidence:
    result = evaluate_resource_window(samples)
    completed_at = _database_now(db)
    payload = {
        "deployment_id": str(deployment_id),
        "checkpoint_id": evidence_key,
        "completed_at": completed_at.isoformat(),
        "policy_version": "phase-1d-f-resource-v1",
        "sample_count": len(samples),
        "reason": result.reason,
        "median_mem_available_bytes": result.median_mem_available_bytes,
        "minimum_mem_available_bytes": result.minimum_mem_available_bytes,
        "samples": [
            {
                "elapsed_ms": sample.elapsed_ms,
                "mem_available_bytes": sample.mem_available_bytes,
                "swap_free_bytes": sample.swap_free_bytes,
                "root_free_bytes": sample.root_free_bytes,
                "docker_free_bytes": sample.docker_free_bytes,
            }
            for sample in samples
        ],
    }
    evidence = _add_evidence(
        db,
        deployment_id=deployment_id,
        evidence_type="resource_gate",
        evidence_key=evidence_key,
        status_code="passed" if result.passed else "failed",
        payload=payload,
        occurred_at=completed_at,
    )
    db.commit()
    db.refresh(evidence)
    return evidence


def acquire_deployment_lock(
    db: Session,
    *,
    deployment_id: UUID,
    owner_identity: str,
    lease_seconds: int,
    evidence_key: str,
    conflict_domain: str | None = None,
) -> ProductionLease:
    if conflict_domain not in {None, PRODUCTION_CONFLICT_DOMAIN}:
        raise DeploymentGateError("canonical_conflict_domain_required")
    executor = _resolve_executor_identity(owner_identity)
    if lease_seconds < 5 or lease_seconds > 900:
        raise DeploymentPolicyError("invalid lock lease")
    if db.get(DeploymentIntent, deployment_id) is None:
        raise DeploymentGateError("unknown_deployment")
    now = _database_now(db)
    lock = db.execute(
        select(DeploymentLock)
        .where(DeploymentLock.conflict_domain == PRODUCTION_CONFLICT_DOMAIN)
        .with_for_update()
    ).scalar_one_or_none()
    acquisition_payload: dict[str, object]
    if lock is None:
        lock = DeploymentLock(
            conflict_domain=PRODUCTION_CONFLICT_DOMAIN,
            environment="production",
            deployment_id=deployment_id,
            owner_identity=executor.service_id,
            fencing_token=1,
            acquired_at=now,
            renewed_at=now,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
        )
        db.add(lock)
        acquisition_payload = {
            "conflict_domain": PRODUCTION_CONFLICT_DOMAIN,
            "owner": executor.service_id,
            "fencing_token": lock.fencing_token,
        }
    elif (
        lock.deployment_id == deployment_id
        and lock.owner_identity == executor.service_id
        and lock.lease_expires_at > now
    ):
        acquisition_payload = {
            "conflict_domain": PRODUCTION_CONFLICT_DOMAIN,
            "owner": executor.service_id,
            "fencing_token": lock.fencing_token,
        }
    elif lock.lease_expires_at <= now:
        if (
            lock.stale_reconciled_at is None
            or lock.stale_reconciled_token != lock.fencing_token
            or not lock.stale_reconciliation_evidence_key
        ):
            db.rollback()
            raise DeploymentGateError("stale_lock_reconciliation_required")
        prior_owner = lock.owner_identity
        prior_token = lock.fencing_token
        lock.deployment_id = deployment_id
        lock.owner_identity = executor.service_id
        lock.fencing_token += 1
        lock.acquired_at = now
        lock.renewed_at = now
        lock.lease_expires_at = now + timedelta(seconds=lease_seconds)
        acquisition_payload = {
            "conflict_domain": PRODUCTION_CONFLICT_DOMAIN,
            "prior_owner": prior_owner,
            "prior_fencing_token": prior_token,
            "new_owner": executor.service_id,
            "new_fencing_token": lock.fencing_token,
            "reconciliation_evidence_key": (
                lock.stale_reconciliation_evidence_key
            ),
        }
    else:
        db.rollback()
        raise DeploymentGateError("lock_conflict")
    _add_evidence(
        db,
        deployment_id=deployment_id,
        evidence_type="lock_acquired",
        evidence_key=evidence_key,
        status_code="recorded",
        payload=acquisition_payload,
        occurred_at=now,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DeploymentGateError("lock_conflict") from exc
    return ProductionLease(
        deployment_id, executor.service_id, lock.fencing_token
    )


def reconcile_expired_production_lock(
    db: Session,
    *,
    observer: AuthoritativeRuntimeObserver,
    evidence_key: str,
) -> None:
    """Reconcile an expired owner before any new owner may take over."""
    lock = db.execute(
        select(DeploymentLock)
        .where(DeploymentLock.conflict_domain == PRODUCTION_CONFLICT_DOMAIN)
        .with_for_update()
    ).scalar_one_or_none()
    if lock is None:
        raise DeploymentGateError("no_production_lock")
    now = _database_now(db)
    if lock.lease_expires_at > now:
        raise DeploymentGateError("lock_not_expired")
    observation = observer.observe(
        deployment_id=lock.deployment_id,
        execution_id=None,
        fencing_token=lock.fencing_token,
    )
    if (
        observation.deployment_id != lock.deployment_id
        or observation.fencing_token != lock.fencing_token
        or observation.state
        in {RuntimeState.IN_PROGRESS, RuntimeState.UNKNOWN}
    ):
        raise DeploymentGateError("stale_owner_runtime_not_reconciled")
    lock.stale_reconciled_at = now
    lock.stale_reconciled_token = lock.fencing_token
    lock.stale_reconciliation_evidence_key = evidence_key
    _add_evidence(
        db,
        deployment_id=lock.deployment_id,
        evidence_type="stale_lock_reconciliation",
        evidence_key=evidence_key,
        status_code="passed",
        payload={
            "prior_owner": lock.owner_identity,
            "prior_fencing_token": lock.fencing_token,
            "runtime_state": observation.state.value,
            "observed_at": observation.observed_at.isoformat(),
        },
        occurred_at=now,
    )
    db.commit()


def renew_deployment_lock(
    db: Session,
    *,
    deployment_id: UUID,
    owner_identity: str,
    lease_seconds: int,
    evidence_key: str,
    fencing_token: int,
) -> None:
    now = _database_now(db)
    lock = db.execute(
        select(DeploymentLock)
        .where(DeploymentLock.conflict_domain == PRODUCTION_CONFLICT_DOMAIN)
        .with_for_update()
    ).scalar_one_or_none()
    if (
        lock is None
        or lock.deployment_id != deployment_id
        or lock.owner_identity != owner_identity
        or lock.fencing_token != fencing_token
        or lock.lease_expires_at <= now
    ):
        db.rollback()
        raise DeploymentGateError("lock_not_owned")
    if lease_seconds < 5 or lease_seconds > 900:
        raise DeploymentPolicyError("invalid lock lease")
    lock.renewed_at = now
    lock.lease_expires_at = now + timedelta(seconds=lease_seconds)
    _add_evidence(
        db,
        deployment_id=deployment_id,
        evidence_type="lock_renewed",
        evidence_key=evidence_key,
        status_code="recorded",
        payload={
            "conflict_domain": PRODUCTION_CONFLICT_DOMAIN,
            "fencing_token": fencing_token,
        },
        occurred_at=now,
    )
    db.commit()


def release_deployment_lock(
    db: Session,
    *,
    deployment_id: UUID,
    owner_identity: str,
    evidence_key: str,
    fencing_token: int,
) -> None:
    lock = db.execute(
        select(DeploymentLock)
        .where(DeploymentLock.conflict_domain == PRODUCTION_CONFLICT_DOMAIN)
        .with_for_update()
    ).scalar_one_or_none()
    if (
        lock is None
        or lock.deployment_id != deployment_id
        or lock.owner_identity != owner_identity
        or lock.fencing_token != fencing_token
    ):
        db.rollback()
        raise DeploymentGateError("lock_not_owned")
    now = _database_now(db)
    _add_evidence(
        db,
        deployment_id=deployment_id,
        evidence_type="lock_released",
        evidence_key=evidence_key,
        status_code="recorded",
        payload={
            "conflict_domain": PRODUCTION_CONFLICT_DOMAIN,
            "fencing_token": fencing_token,
        },
        occurred_at=now,
    )
    db.delete(lock)
    db.commit()


def _require_passed_evidence(
    db: Session, deployment_id: UUID, evidence_type: str
) -> DeploymentEvidence:
    evidence = db.execute(
        select(DeploymentEvidence)
        .where(
            DeploymentEvidence.deployment_id == deployment_id,
            DeploymentEvidence.evidence_type == evidence_type,
        )
        .order_by(DeploymentEvidence.occurred_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if evidence is None or evidence.status_code != "passed":
        raise DeploymentGateError(f"missing_{evidence_type}")
    return evidence


def _require_fresh_evidence(
    db: Session,
    *,
    deployment_id: UUID,
    evidence_type: str,
    max_age_seconds: int,
) -> DeploymentEvidence:
    evidence = _require_passed_evidence(db, deployment_id, evidence_type)
    now = _database_now(db)
    if evidence.occurred_at + timedelta(seconds=max_age_seconds) < now:
        raise DeploymentGateError(f"stale_{evidence_type}")
    if evidence_type == "predeploy_backup":
        try:
            created_at = datetime.fromisoformat(evidence.payload["created_at"])
        except (KeyError, TypeError, ValueError):
            raise DeploymentGateError("invalid_backup_timestamp") from None
        if (
            created_at.tzinfo is None
            or created_at + timedelta(seconds=BACKUP_EVIDENCE_MAX_AGE_SECONDS)
            < now
        ):
            raise DeploymentGateError("stale_predeploy_backup")
    if evidence_type == "resource_gate":
        try:
            completed_at = datetime.fromisoformat(
                evidence.payload["completed_at"]
            )
        except (KeyError, TypeError, ValueError):
            raise DeploymentGateError(
                "invalid_resource_gate_timestamp"
            ) from None
        if (
            completed_at.tzinfo is None
            or completed_at
            + timedelta(seconds=RESOURCE_EVIDENCE_MAX_AGE_SECONDS)
            < now
            or evidence.occurred_at != completed_at
        ):
            raise DeploymentGateError("stale_resource_gate")
    return evidence


@dataclass(frozen=True)
class _AuthoritativeExecution:
    deployment_id: UUID
    execution_id: UUID
    executor_identity: str
    fencing_token: int
    image_reference: str
    artifact_digest: str
    target_sha: str
    services: tuple[str, ...]
    expected_current_revision: str | None


def _load_authoritative_execution(
    db: Session,
    *,
    deployment_id: UUID,
    execution_id: UUID,
    executor_identity: str,
    fencing_token: int,
    ci_verifier: ProtectedMainCiVerifier,
) -> tuple[_AuthoritativeExecution, DeploymentIntent]:
    """Reload every immutable authorization fact immediately before mutation."""
    now = _database_now(db)
    intent = db.execute(
        select(DeploymentIntent)
        .where(DeploymentIntent.id == deployment_id)
        .with_for_update()
    ).scalar_one_or_none()
    attempt = db.execute(
        select(DeploymentExecutionAttempt).where(
            DeploymentExecutionAttempt.id == execution_id,
            DeploymentExecutionAttempt.deployment_id == deployment_id,
        )
    ).scalar_one_or_none()
    artifact = db.execute(
        select(DeploymentArtifact).where(
            DeploymentArtifact.deployment_id == deployment_id
        )
    ).scalar_one_or_none()
    acceptance = db.execute(
        select(StagingAcceptance).where(
            StagingAcceptance.deployment_id == deployment_id
        )
    ).scalar_one_or_none()
    binding = db.execute(
        select(DeploymentApprovalBinding).where(
            DeploymentApprovalBinding.deployment_id == deployment_id
        )
    ).scalar_one_or_none()
    if attempt is None:
        raise DeploymentGateError("forged_or_stale_execution_authorization")
    if any(value is None for value in (intent, artifact, acceptance, binding)):
        raise DeploymentGateError("authoritative_execution_state_incomplete")
    if (
        attempt.executor_identity != executor_identity
        or attempt.fencing_token != fencing_token
    ):
        raise DeploymentGateError("forged_or_stale_execution_authorization")
    invalidation = db.execute(
        select(StagingAcceptanceInvalidation).where(
            StagingAcceptanceInvalidation.acceptance_id == acceptance.id
        )
    ).scalar_one_or_none()
    if invalidation is not None or acceptance.valid_until <= now:
        raise DeploymentGateError("stale_or_invalidated_staging_acceptance")
    request = db.execute(
        select(ApprovalRequest)
        .where(ApprovalRequest.id == binding.approval_request_id)
        .with_for_update()
    ).scalar_one_or_none()
    decision = db.execute(
        select(ApprovalDecision)
        .where(ApprovalDecision.request_id == binding.approval_request_id)
        .with_for_update()
    ).scalar_one_or_none()
    consumption = db.execute(
        select(DeploymentApprovalConsumption).where(
            DeploymentApprovalConsumption.deployment_id == deployment_id,
            DeploymentApprovalConsumption.approval_request_id
            == binding.approval_request_id,
        )
    ).scalar_one_or_none()
    lock = db.execute(
        select(DeploymentLock)
        .where(DeploymentLock.conflict_domain == PRODUCTION_CONFLICT_DOMAIN)
        .with_for_update()
    ).scalar_one_or_none()
    if any(value is None for value in (request, decision, consumption, lock)):
        raise DeploymentGateError("authoritative_execution_state_incomplete")
    expected = (
        intent.repository,
        intent.target_sha,
        intent.target_environment,
        intent.action_code,
        intent.artifact_digest,
    )
    if (
        (
            binding.repository,
            binding.target_sha,
            binding.target_environment,
            binding.action_code,
            binding.artifact_digest,
        )
        != expected
        or request.deployment_id != intent.id
        or request.artifact_digest != intent.artifact_digest
        or request.user_id != intent.user_id
        or binding.requester_user_id != intent.user_id
        or decision.decision_code != "approved"
        or decision.actor_user_id == intent.user_id
        or request.expires_at <= now
        or decision.decided_at >= request.expires_at
    ):
        raise DeploymentGateError("approval_binding_or_separation_invalid")
    requester = _resolve_human_identity(db, intent.user_id)
    approver = _resolve_human_identity(db, decision.actor_user_id)
    if (
        binding.requester_identity_fingerprint != requester.fingerprint
        or approver.user_id == requester.user_id
    ):
        raise DeploymentGateError("canonical_identity_validation_failed")
    validate_immutable_artifact(
        digest=artifact.digest, image_reference=artifact.image_reference
    )
    if (
        artifact.repository != intent.repository
        or artifact.target_sha != intent.target_sha
        or artifact.digest != intent.artifact_digest
        or acceptance.repository != intent.repository
        or acceptance.target_sha != intent.target_sha
        or acceptance.artifact_digest != artifact.digest
        or acceptance.config_fingerprint != intent.config_fingerprint
    ):
        raise DeploymentGateError("authoritative_artifact_or_staging_mismatch")
    verified = ci_verifier.verify(
        repository=intent.repository,
        target_sha=intent.target_sha,
        run_id=artifact.ci_run_id,
    )
    if (
        not _valid_verified_ci(
            verified, intent.repository, intent.target_sha, artifact.ci_run_id
        )
        or verified.verified_at
        + timedelta(seconds=CI_EVIDENCE_MAX_AGE_SECONDS)
        < now
        or artifact.ci_repository != verified.repository
        or artifact.ci_workflow_name != verified.workflow_name
    ):
        raise DeploymentGateError("stale_or_inauthentic_ci_evidence")
    services = validate_service_set(tuple(intent.service_set))
    if services != tuple(intent.service_set):
        raise DeploymentGateError("service_set_not_canonical")
    validate_migration_plan(
        MigrationRisk(intent.migration_risk),
        migration_revision=intent.migration_revision,
        rollback_runbook_ref=intent.rollback_runbook_ref,
    )
    _require_fresh_evidence(
        db,
        deployment_id=deployment_id,
        evidence_type="predeploy_backup",
        max_age_seconds=BACKUP_EVIDENCE_MAX_AGE_SECONDS,
    )
    _require_fresh_evidence(
        db,
        deployment_id=deployment_id,
        evidence_type="resource_gate",
        max_age_seconds=RESOURCE_EVIDENCE_MAX_AGE_SECONDS,
    )
    if (
        lock.deployment_id != deployment_id
        or lock.owner_identity != executor_identity
        or lock.fencing_token != fencing_token
        or lock.lease_expires_at <= now
    ):
        raise DeploymentGateError("stale_or_incorrect_fencing_token")
    expected_current_revision = None
    if intent.intent_kind == "rollback":
        rollback = db.execute(
            select(DeploymentRollback).where(
                DeploymentRollback.deployment_id == deployment_id
            )
        ).scalar_one_or_none()
        if (
            rollback is None
            or rollback.rollback_target_sha != intent.target_sha
            or rollback.rollback_artifact_digest != intent.artifact_digest
        ):
            raise DeploymentGateError("rollback_target_mismatch")
        expected_current_revision = rollback.current_production_sha
    return (
        _AuthoritativeExecution(
            deployment_id=deployment_id,
            execution_id=execution_id,
            executor_identity=executor_identity,
            fencing_token=fencing_token,
            image_reference=artifact.image_reference,
            artifact_digest=artifact.digest,
            target_sha=intent.target_sha,
            services=services,
            expected_current_revision=expected_current_revision,
        ),
        intent,
    )


def start_production_deployment(
    db: Session,
    *,
    deployment_id: UUID,
    owner_identity: str,
    fencing_token: int,
) -> StartOutcome:
    """Consume approval once and persist an attempt; never return execution authority."""
    executor = _resolve_executor_identity(owner_identity)
    intent = db.execute(
        select(DeploymentIntent)
        .where(DeploymentIntent.id == deployment_id)
        .with_for_update()
    ).scalar_one_or_none()
    if intent is None:
        raise DeploymentGateError("unknown_deployment")
    existing_attempt = db.execute(
        select(DeploymentExecutionAttempt).where(
            DeploymentExecutionAttempt.deployment_id == deployment_id
        )
    ).scalar_one_or_none()
    if existing_attempt is not None:
        return StartOutcome(
            "reconciliation_required",
            execution_id=existing_attempt.id,
            fencing_token=existing_attempt.fencing_token,
        )
    # Reuse the immediate-pre-mutation loader later; this stage only binds a
    # canonical approval decision and current canonical lease atomically.
    now = _database_now(db)
    binding = db.execute(
        select(DeploymentApprovalBinding).where(
            DeploymentApprovalBinding.deployment_id == deployment_id
        )
    ).scalar_one_or_none()
    if binding is None:
        raise DeploymentGateError("missing_approval")
    request = db.execute(
        select(ApprovalRequest)
        .where(ApprovalRequest.id == binding.approval_request_id)
        .with_for_update()
    ).scalar_one_or_none()
    decision = db.execute(
        select(ApprovalDecision)
        .where(ApprovalDecision.request_id == binding.approval_request_id)
        .with_for_update()
    ).scalar_one_or_none()
    lock = db.execute(
        select(DeploymentLock)
        .where(DeploymentLock.conflict_domain == PRODUCTION_CONFLICT_DOMAIN)
        .with_for_update()
    ).scalar_one_or_none()
    if (
        request is None
        or decision is None
        or lock is None
        or lock.deployment_id != deployment_id
        or lock.owner_identity != executor.service_id
        or lock.fencing_token != fencing_token
        or lock.lease_expires_at <= now
    ):
        raise DeploymentGateError("deployment_lock_or_approval_invalid")
    if (
        request.deployment_id != deployment_id
        or request.artifact_digest != intent.artifact_digest
        or request.expires_at <= now
        or decision.decided_at >= request.expires_at
        or decision.decision_code != "approved"
        or decision.actor_user_id == intent.user_id
    ):
        raise DeploymentGateError("approval_binding_or_separation_invalid")
    expected_state = (
        "PRODUCTION_DEPLOYING"
        if intent.intent_kind == "deploy"
        else "ROLLBACK_DEPLOYING"
    )
    transition = _append_state_event(
        db,
        deployment_id=deployment_id,
        to_state=expected_state,
        event_code="approval_consumed_and_execution_prepared",
        actor_fingerprint=intent.requested_by_identity_fingerprint,
        evidence={"fencing_token": fencing_token},
        occurred_at=now,
    )
    consumption = DeploymentApprovalConsumption(
        deployment_id=deployment_id,
        approval_request_id=request.id,
        approval_decision_id=decision.id,
        state_event_id=transition.id,
        consumed_at=now,
    )
    attempt = DeploymentExecutionAttempt(
        deployment_id=deployment_id,
        execution_key=f"execution-{uuid4()}",
        executor_identity=executor.service_id,
        fencing_token=fencing_token,
    )
    db.add_all((consumption, attempt))
    db.flush()
    _add_evidence(
        db,
        deployment_id=deployment_id,
        evidence_type="execution_attempt",
        evidence_key=attempt.execution_key,
        status_code="recorded",
        payload={
            "execution_id": str(attempt.id),
            "executor_identity": executor.service_id,
            "fencing_token": fencing_token,
        },
        occurred_at=now,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DeploymentGateError("concurrent_deployment_attempt") from exc
    return StartOutcome(
        "execution_prepared",
        execution_id=attempt.id,
        fencing_token=fencing_token,
    )


def prepare_authoritative_mutation(
    db: Session,
    *,
    deployment_id: UUID,
    execution_id: UUID,
    executor_identity: str,
    fencing_token: int,
    ci_verifier: ProtectedMainCiVerifier,
    runtime_observer: AuthoritativeRuntimeObserver,
) -> _AuthoritativeExecution:
    execution, intent = _load_authoritative_execution(
        db,
        deployment_id=deployment_id,
        execution_id=execution_id,
        executor_identity=executor_identity,
        fencing_token=fencing_token,
        ci_verifier=ci_verifier,
    )
    existing_started = db.execute(
        select(DeploymentEvidence).where(
            DeploymentEvidence.deployment_id == deployment_id,
            DeploymentEvidence.evidence_type == "mutation_started",
        )
    ).scalar_one_or_none()
    observation = runtime_observer.observe(
        deployment_id=deployment_id,
        execution_id=execution_id,
        fencing_token=fencing_token,
    )
    if (
        observation.deployment_id != deployment_id
        or observation.execution_id != execution_id
        or observation.fencing_token != fencing_token
    ):
        raise DeploymentGateError("runtime_observation_identity_mismatch")
    if (
        existing_started is not None
        or observation.state != RuntimeState.NOT_STARTED
    ):
        raise DeploymentGateError("execution_reconciliation_required")
    if (
        intent.intent_kind == "rollback"
        and observation.observed_revision
        != execution.expected_current_revision
    ):
        raise DeploymentGateError("rollback_current_revision_mismatch")
    _add_evidence(
        db,
        deployment_id=deployment_id,
        evidence_type="mutation_started",
        evidence_key=f"mutation-{execution_id}",
        status_code="recorded",
        payload={
            "execution_id": str(execution_id),
            "fencing_token": fencing_token,
            "runtime_state": observation.state.value,
        },
    )
    db.commit()
    return execution


def reconcile_authoritative_execution(
    db: Session,
    *,
    deployment_id: UUID,
    execution_id: UUID,
    executor_identity: str,
    fencing_token: int,
    ci_verifier: ProtectedMainCiVerifier,
    runtime_observer: AuthoritativeRuntimeObserver,
    evidence_key: str,
) -> str:
    execution, intent = _load_authoritative_execution(
        db,
        deployment_id=deployment_id,
        execution_id=execution_id,
        executor_identity=executor_identity,
        fencing_token=fencing_token,
        ci_verifier=ci_verifier,
    )
    observation = runtime_observer.observe(
        deployment_id=deployment_id,
        execution_id=execution_id,
        fencing_token=fencing_token,
    )
    if observation.state in {
        RuntimeState.NOT_STARTED,
        RuntimeState.IN_PROGRESS,
        RuntimeState.MUTATION_COMPLETED_HEALTH_UNKNOWN,
        RuntimeState.UNKNOWN,
    }:
        _add_evidence(
            db,
            deployment_id=deployment_id,
            evidence_type="interruption_reconciliation",
            evidence_key=evidence_key,
            status_code="pending"
            if observation.state != RuntimeState.UNKNOWN
            else "failed",
            payload={
                "execution_id": str(execution_id),
                "runtime_state": observation.state.value,
                "observed_at": observation.observed_at.isoformat(),
            },
        )
        db.commit()
        return observation.state.value
    succeeded = (
        observation.state == RuntimeState.HEALTHY
        and observation.observed_digest == execution.artifact_digest
        and observation.observed_revision == intent.target_sha
        and (
            intent.migration_revision is None
            or observation.migration_revision == intent.migration_revision
        )
        and health_checks_are_complete(
            execution.services, observation.health_checks
        )
    )
    final_state = (
        "PRODUCTION_HEALTHY"
        if intent.intent_kind == "deploy" and succeeded
        else "ROLLED_BACK"
        if intent.intent_kind == "rollback" and succeeded
        else "PRODUCTION_FAILED"
        if intent.intent_kind == "deploy"
        else "ROLLBACK_FAILED"
    )
    now = _database_now(db)
    _add_evidence(
        db,
        deployment_id=deployment_id,
        evidence_type="interruption_reconciliation",
        evidence_key=evidence_key,
        status_code="passed" if succeeded else "failed",
        payload={
            "execution_id": str(execution_id),
            "runtime_state": observation.state.value,
            "observed_digest": observation.observed_digest,
            "observed_revision": observation.observed_revision,
            "health_checks": observation.health_checks,
            "migration_revision": observation.migration_revision,
            "observed_at": observation.observed_at.isoformat(),
        },
        occurred_at=now,
    )
    _append_state_event(
        db,
        deployment_id=deployment_id,
        to_state=final_state,
        event_code="runtime_reconciled",
        actor_fingerprint=intent.requested_by_identity_fingerprint,
        evidence={"execution_id": str(execution_id), "succeeded": succeeded},
        occurred_at=now,
    )
    db.commit()
    return final_state


def complete_production_execution(*args, **kwargs) -> str:
    """Caller-supplied completion reports are intentionally not authoritative."""
    del args, kwargs
    raise DeploymentGateError("caller_supplied_runtime_result_forbidden")


def execute_validated_deployment(*args, **kwargs) -> str:
    del args, kwargs
    raise DeploymentGateError("caller_constructed_execution_forbidden")


def reconcile_interrupted_execution(*args, **kwargs) -> str:
    del args, kwargs
    raise DeploymentGateError("caller_supplied_runtime_result_forbidden")
