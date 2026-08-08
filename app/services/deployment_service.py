import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from uuid import UUID

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
    require_transition,
    validate_immutable_artifact,
    validate_migration_plan,
    validate_service_set,
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
    run_id: int
    event: str
    status: str
    conclusion: str
    head_sha: str
    quality_gate_conclusion: str
    protected_main_verified: bool


@dataclass(frozen=True)
class ValidatedExecution:
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
    execution: ValidatedExecution | None = None


@dataclass(frozen=True)
class ExecutionResult:
    succeeded: bool
    observed_digest: str | None
    before_revision: str
    after_revision: str | None
    health_checks: dict[str, bool]
    failure_code: str | None = None


class ProductionExecutor(Protocol):
    def deploy(self, execution: ValidatedExecution) -> ExecutionResult: ...


def _database_now(db: Session):
    return db.execute(select(func.clock_timestamp())).scalar_one()


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
    user_id: UUID,
    intent_kind: str,
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
) -> DeploymentIntent:
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
        requester_fingerprint=requester_fingerprint,
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
        requester_fingerprint,
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
        requested_by_identity_fingerprint=requester_fingerprint,
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
        actor_fingerprint=requester_fingerprint,
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
    ci: CiEvidence,
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
    if (
        ci.run_id < 1
        or ci.event != "push"
        or ci.status != "completed"
        or ci.conclusion != "success"
        or ci.head_sha != target_sha
        or ci.quality_gate_conclusion != "success"
        or not ci.protected_main_verified
    ):
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
            existing.provenance,
        )
        expected = (
            repository,
            target_sha,
            digest,
            image_reference,
            ci.run_id,
            ci.event,
            ci.status,
            ci.conclusion,
            ci.head_sha,
            ci.quality_gate_conclusion,
            ci.protected_main_verified,
            provenance,
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
        ci_run_id=ci.run_id,
        ci_event=ci.event,
        ci_status=ci.status,
        ci_conclusion=ci.conclusion,
        ci_head_sha=ci.head_sha,
        quality_gate_conclusion=ci.quality_gate_conclusion,
        protected_main_verified=ci.protected_main_verified,
        provenance=provenance,
    )
    db.add(artifact)
    _append_state_event(
        db,
        deployment_id=deployment_id,
        to_state="ARTIFACT_RECORDED",
        event_code="artifact_recorded",
        actor_fingerprint=actor_fingerprint,
        evidence={"digest": digest, "ci_run_id": ci.run_id},
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


def bind_production_approval(
    db: Session,
    *,
    deployment_id: UUID,
    approval_request_id: UUID,
    requester_fingerprint: str,
) -> DeploymentApprovalBinding:
    intent = db.execute(
        select(DeploymentIntent)
        .where(DeploymentIntent.id == deployment_id)
        .with_for_update()
    ).scalar_one_or_none()
    request = db.get(ApprovalRequest, approval_request_id)
    if intent is None or request is None:
        raise DeploymentGateError("missing_approval")
    if requester_fingerprint != intent.requested_by_identity_fingerprint:
        raise DeploymentGateError("requester_mismatch")
    expected = (
        intent.repository,
        intent.target_sha,
        intent.target_environment,
        intent.action_code,
    )
    actual = (
        request.repository,
        request.target_sha,
        request.target_environment,
        request.action_code,
    )
    if actual != expected:
        raise DeploymentGateError("approval_binding_mismatch")
    existing_decision = db.execute(
        select(ApprovalDecision).where(
            ApprovalDecision.request_id == approval_request_id
        )
    ).scalar_one_or_none()
    if existing_decision is not None:
        raise DeploymentGateError("approval_already_decided_before_binding")
    existing = db.execute(
        select(DeploymentApprovalBinding).where(
            DeploymentApprovalBinding.deployment_id == deployment_id
        )
    ).scalar_one_or_none()
    if existing is not None:
        if (
            existing.approval_request_id != approval_request_id
            or existing.requester_identity_fingerprint != requester_fingerprint
        ):
            raise DeploymentGateError("approval_binding_replay_conflict")
        return existing
    binding = DeploymentApprovalBinding(
        deployment_id=deployment_id,
        approval_request_id=approval_request_id,
        repository=intent.repository,
        target_sha=intent.target_sha,
        target_environment=intent.target_environment,
        action_code=intent.action_code,
        artifact_digest=intent.artifact_digest,
        requester_identity_fingerprint=requester_fingerprint,
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
        actor_fingerprint=requester_fingerprint,
        evidence={"approval_request_id": str(approval_request_id)},
    )
    db.commit()
    db.refresh(binding)
    return binding


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
    if evidence_type == "predeploy_backup" and status_code == "passed":
        required = {"backup_id", "sha256", "catalog_validated"}
        if (
            set(payload) != required
            or payload["catalog_validated"] is not True
        ):
            raise DeploymentGateError("invalid_backup_evidence")
        if not re.fullmatch(r"[0-9a-f]{64}", str(payload["sha256"])):
            raise DeploymentGateError("invalid_backup_checksum")
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
    payload = {
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
    return record_deployment_evidence(
        db,
        deployment_id=deployment_id,
        evidence_type="resource_gate",
        evidence_key=evidence_key,
        status_code="passed" if result.passed else "failed",
        payload=payload,
    )


def acquire_deployment_lock(
    db: Session,
    *,
    deployment_id: UUID,
    owner_identity: str,
    lease_seconds: int,
    evidence_key: str,
    conflict_domain: str = "production",
) -> str:
    if not OWNER_PATTERN.fullmatch(owner_identity):
        raise DeploymentPolicyError("invalid lock owner")
    if lease_seconds < 5 or lease_seconds > 900:
        raise DeploymentPolicyError("invalid lock lease")
    if db.get(DeploymentIntent, deployment_id) is None:
        raise DeploymentGateError("unknown_deployment")
    now = _database_now(db)
    lock = db.execute(
        select(DeploymentLock)
        .where(DeploymentLock.conflict_domain == conflict_domain)
        .with_for_update()
    ).scalar_one_or_none()
    evidence_type = "lock_acquired"
    result = "acquired"
    if lock is None:
        lock = DeploymentLock(
            conflict_domain=conflict_domain,
            environment="production",
            deployment_id=deployment_id,
            owner_identity=owner_identity,
            acquired_at=now,
            renewed_at=now,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
        )
        db.add(lock)
    elif (
        lock.deployment_id == deployment_id
        and lock.owner_identity == owner_identity
        and lock.lease_expires_at > now
    ):
        result = "already_owned"
    elif lock.lease_expires_at <= now:
        lock.deployment_id = deployment_id
        lock.owner_identity = owner_identity
        lock.acquired_at = now
        lock.renewed_at = now
        lock.lease_expires_at = now + timedelta(seconds=lease_seconds)
        evidence_type = "stale_lock_reconciled"
        result = "stale_reconciled"
    else:
        db.rollback()
        raise DeploymentGateError("lock_conflict")
    _add_evidence(
        db,
        deployment_id=deployment_id,
        evidence_type=evidence_type,
        evidence_key=evidence_key,
        status_code="recorded",
        payload={"conflict_domain": conflict_domain, "result": result},
        occurred_at=now,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DeploymentGateError("lock_conflict") from exc
    return result


def renew_deployment_lock(
    db: Session,
    *,
    deployment_id: UUID,
    owner_identity: str,
    lease_seconds: int,
    evidence_key: str,
    conflict_domain: str = "production",
) -> None:
    now = _database_now(db)
    lock = db.execute(
        select(DeploymentLock)
        .where(DeploymentLock.conflict_domain == conflict_domain)
        .with_for_update()
    ).scalar_one_or_none()
    if (
        lock is None
        or lock.deployment_id != deployment_id
        or lock.owner_identity != owner_identity
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
        payload={"conflict_domain": conflict_domain},
        occurred_at=now,
    )
    db.commit()


def release_deployment_lock(
    db: Session,
    *,
    deployment_id: UUID,
    owner_identity: str,
    evidence_key: str,
    conflict_domain: str = "production",
) -> None:
    lock = db.execute(
        select(DeploymentLock)
        .where(DeploymentLock.conflict_domain == conflict_domain)
        .with_for_update()
    ).scalar_one_or_none()
    if (
        lock is None
        or lock.deployment_id != deployment_id
        or lock.owner_identity != owner_identity
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
        payload={"conflict_domain": conflict_domain},
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


def _validated_execution(
    *,
    intent: DeploymentIntent,
    artifact: DeploymentArtifact,
    owner_identity: str,
    consumption_id: UUID,
    expected_current_revision: str | None = None,
) -> ValidatedExecution:
    return ValidatedExecution(
        deployment_id=intent.id,
        intent_kind=intent.intent_kind,
        repository=intent.repository,
        target_sha=intent.target_sha,
        environment=intent.target_environment,
        action_code=intent.action_code,
        artifact_digest=artifact.digest,
        image_reference=artifact.image_reference,
        services=tuple(intent.service_set),
        lock_owner=owner_identity,
        approval_consumption_id=consumption_id,
        expected_current_revision=expected_current_revision,
    )


def start_production_deployment(
    db: Session,
    *,
    deployment_id: UUID,
    owner_identity: str,
    conflict_domain: str = "production",
) -> StartOutcome:
    intent = db.execute(
        select(DeploymentIntent)
        .where(DeploymentIntent.id == deployment_id)
        .with_for_update()
    ).scalar_one_or_none()
    if intent is None:
        raise DeploymentGateError("unknown_deployment")
    artifact = db.execute(
        select(DeploymentArtifact).where(
            DeploymentArtifact.deployment_id == deployment_id
        )
    ).scalar_one_or_none()
    if artifact is None:
        raise DeploymentGateError("missing_artifact")
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
    existing_consumption = db.execute(
        select(DeploymentApprovalConsumption).where(
            DeploymentApprovalConsumption.deployment_id == deployment_id
        )
    ).scalar_one_or_none()
    latest = _latest_state_event(db, deployment_id)
    deploying_state = (
        "PRODUCTION_DEPLOYING"
        if intent.intent_kind == "deploy"
        else "ROLLBACK_DEPLOYING"
    )
    if existing_consumption is not None:
        replay_now = _database_now(db)
        replay_lock = db.execute(
            select(DeploymentLock)
            .where(DeploymentLock.conflict_domain == conflict_domain)
            .with_for_update()
        ).scalar_one_or_none()
        if (
            replay_lock is None
            or replay_lock.deployment_id != deployment_id
            or replay_lock.owner_identity != owner_identity
            or replay_lock.lease_expires_at <= replay_now
        ):
            raise DeploymentGateError("deployment_lock_invalid")
        if latest is not None and latest.to_state == deploying_state:
            return StartOutcome(
                "already_started",
                _validated_execution(
                    intent=intent,
                    artifact=artifact,
                    owner_identity=owner_identity,
                    consumption_id=existing_consumption.id,
                    expected_current_revision=expected_current_revision,
                ),
            )
        raise DeploymentGateError("approval_consumed")

    now = _database_now(db)
    acceptance = db.execute(
        select(StagingAcceptance).where(
            StagingAcceptance.deployment_id == deployment_id
        )
    ).scalar_one_or_none()
    if acceptance is None:
        raise DeploymentGateError("missing_staging_acceptance")
    invalidation = db.execute(
        select(StagingAcceptanceInvalidation).where(
            StagingAcceptanceInvalidation.acceptance_id == acceptance.id
        )
    ).scalar_one_or_none()
    if invalidation is not None:
        raise DeploymentGateError("invalidated_staging_acceptance")
    if acceptance.valid_until <= now:
        raise DeploymentGateError("stale_staging_acceptance")
    if (
        acceptance.repository != intent.repository
        or acceptance.target_sha != intent.target_sha
        or acceptance.artifact_digest != intent.artifact_digest
        or acceptance.artifact_digest != artifact.digest
        or acceptance.config_fingerprint != intent.config_fingerprint
    ):
        raise DeploymentGateError("staging_binding_mismatch")

    binding = db.execute(
        select(DeploymentApprovalBinding).where(
            DeploymentApprovalBinding.deployment_id == deployment_id
        )
    ).scalar_one_or_none()
    if binding is None:
        raise DeploymentGateError("missing_approval")
    expected_binding = (
        intent.repository,
        intent.target_sha,
        intent.target_environment,
        intent.action_code,
        intent.artifact_digest,
    )
    actual_binding = (
        binding.repository,
        binding.target_sha,
        binding.target_environment,
        binding.action_code,
        binding.artifact_digest,
    )
    if actual_binding != expected_binding:
        raise DeploymentGateError("approval_binding_mismatch")
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
    if request is None or decision is None:
        raise DeploymentGateError("missing_approval")
    if decision.decision_code != "approved":
        raise DeploymentGateError("approval_not_approved")
    if request.expires_at <= now or decision.decided_at >= request.expires_at:
        raise DeploymentGateError("approval_expired")
    if (
        decision.approver_identity_fingerprint
        == binding.requester_identity_fingerprint
    ):
        raise DeploymentGateError("self_approval")

    validate_immutable_artifact(
        digest=artifact.digest,
        image_reference=artifact.image_reference,
    )
    if (
        artifact.repository != intent.repository
        or artifact.target_sha != intent.target_sha
        or artifact.ci_head_sha != intent.target_sha
        or artifact.ci_event != "push"
        or artifact.ci_status != "completed"
        or artifact.ci_conclusion != "success"
        or artifact.quality_gate_conclusion != "success"
        or not artifact.protected_main_verified
    ):
        raise DeploymentGateError("invalid_ci_evidence")
    services = validate_service_set(tuple(intent.service_set))
    if services != tuple(intent.service_set):
        raise DeploymentGateError("service_set_not_canonical")
    validate_migration_plan(
        MigrationRisk(intent.migration_risk),
        migration_revision=intent.migration_revision,
        rollback_runbook_ref=intent.rollback_runbook_ref,
    )
    _require_passed_evidence(db, deployment_id, "predeploy_backup")
    _require_passed_evidence(db, deployment_id, "resource_gate")
    lock = db.execute(
        select(DeploymentLock)
        .where(DeploymentLock.conflict_domain == conflict_domain)
        .with_for_update()
    ).scalar_one_or_none()
    if (
        lock is None
        or lock.deployment_id != deployment_id
        or lock.owner_identity != owner_identity
        or lock.environment != "production"
        or lock.lease_expires_at <= now
    ):
        raise DeploymentGateError("deployment_lock_invalid")
    transition = _append_state_event(
        db,
        deployment_id=deployment_id,
        to_state=deploying_state,
        event_code="approval_consumed_and_deployment_started",
        actor_fingerprint=intent.requested_by_identity_fingerprint,
        evidence={
            "artifact_digest": artifact.digest,
            "services": list(services),
        },
        occurred_at=now,
    )
    consumption = DeploymentApprovalConsumption(
        deployment_id=deployment_id,
        approval_request_id=request.id,
        approval_decision_id=decision.id,
        state_event_id=transition.id,
        consumed_at=now,
    )
    db.add(consumption)
    _add_evidence(
        db,
        deployment_id=deployment_id,
        evidence_type="executor_started",
        evidence_key=f"start-{transition.id}",
        status_code="recorded",
        payload={
            "artifact_digest": artifact.digest,
            "services": list(services),
        },
        occurred_at=now,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DeploymentGateError("concurrent_deployment_attempt") from exc
    return StartOutcome(
        "started",
        _validated_execution(
            intent=intent,
            artifact=artifact,
            owner_identity=owner_identity,
            consumption_id=consumption.id,
            expected_current_revision=expected_current_revision,
        ),
    )


def complete_production_execution(
    db: Session,
    *,
    execution: ValidatedExecution,
    result: ExecutionResult,
    evidence_key: str,
    reconciled: bool = False,
) -> str:
    intent = db.execute(
        select(DeploymentIntent)
        .where(DeploymentIntent.id == execution.deployment_id)
        .with_for_update()
    ).scalar_one_or_none()
    if intent is None:
        raise DeploymentGateError("unknown_deployment")
    latest = _latest_state_event(db, intent.id)
    expected_state = (
        "PRODUCTION_DEPLOYING"
        if intent.intent_kind == "deploy"
        else "ROLLBACK_DEPLOYING"
    )
    if latest is None or latest.to_state != expected_state:
        raise DeploymentGateError("execution_not_in_progress")
    consumption = db.get(
        DeploymentApprovalConsumption,
        execution.approval_consumption_id,
    )
    if consumption is None or consumption.deployment_id != intent.id:
        raise DeploymentGateError("invalid_execution_authorization")
    lock = db.execute(
        select(DeploymentLock)
        .where(DeploymentLock.conflict_domain == "production")
        .with_for_update()
    ).scalar_one_or_none()
    now = _database_now(db)
    if (
        lock is None
        or lock.deployment_id != intent.id
        or lock.owner_identity != execution.lock_owner
        or lock.lease_expires_at <= now
    ):
        raise DeploymentGateError("deployment_lock_invalid")
    digest_matches = result.observed_digest == execution.artifact_digest
    revision_matches = result.after_revision == intent.target_sha
    before_revision_valid = bool(SHA_PATTERN.fullmatch(result.before_revision))
    if intent.intent_kind == "rollback":
        rollback = db.execute(
            select(DeploymentRollback).where(
                DeploymentRollback.deployment_id == intent.id
            )
        ).scalar_one_or_none()
        before_revision_valid = (
            before_revision_valid
            and rollback is not None
            and result.before_revision == rollback.current_production_sha
            and execution.expected_current_revision
            == rollback.current_production_sha
        )
    health_passed = bool(result.health_checks) and all(
        result.health_checks.values()
    )
    succeeded = (
        result.succeeded
        and digest_matches
        and revision_matches
        and before_revision_valid
        and health_passed
    )
    if intent.intent_kind == "deploy":
        final_state = (
            "PRODUCTION_HEALTHY" if succeeded else "PRODUCTION_FAILED"
        )
    else:
        final_state = "ROLLED_BACK" if succeeded else "ROLLBACK_FAILED"
    evidence_type = (
        "interruption_reconciliation" if reconciled else "executor_result"
    )
    _add_evidence(
        db,
        deployment_id=intent.id,
        evidence_type=evidence_type,
        evidence_key=evidence_key,
        status_code="passed" if succeeded else "failed",
        payload={
            "observed_digest": result.observed_digest,
            "before_revision": result.before_revision,
            "after_revision": result.after_revision,
            "health_checks": result.health_checks,
            "failure_code": result.failure_code,
        },
        occurred_at=now,
    )
    _append_state_event(
        db,
        deployment_id=intent.id,
        to_state=final_state,
        event_code=(
            "execution_reconciled" if reconciled else "execution_completed"
        ),
        actor_fingerprint=intent.requested_by_identity_fingerprint,
        evidence={"succeeded": succeeded},
        occurred_at=now,
    )
    db.commit()
    return final_state


def execute_validated_deployment(
    db: Session,
    *,
    execution: ValidatedExecution,
    executor: ProductionExecutor,
    evidence_key: str,
) -> str:
    validate_immutable_artifact(
        digest=execution.artifact_digest,
        image_reference=execution.image_reference,
    )
    validate_service_set(execution.services)
    result = executor.deploy(execution)
    return complete_production_execution(
        db,
        execution=execution,
        result=result,
        evidence_key=evidence_key,
    )


def reconcile_interrupted_execution(
    db: Session,
    *,
    execution: ValidatedExecution,
    observed_result: ExecutionResult,
    evidence_key: str,
) -> str:
    return complete_production_execution(
        db,
        execution=execution,
        result=observed_result,
        evidence_key=evidence_key,
        reconciled=True,
    )
