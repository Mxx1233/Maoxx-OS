"""Minimal typed principal and versioned external-identity authority."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import ExternalIdentity, Principal
from app.services.deployment_authority import external_identity_fingerprint


class IdentityAuthorityError(RuntimeError):
    pass


def provision_principal(
    db,
    *,
    principal_type: str,
    identity_key: str,
    user_id: UUID | None,
) -> Principal:
    if principal_type not in {"HUMAN", "SERVICE", "EXECUTOR"}:
        raise IdentityAuthorityError("invalid_principal_type")
    if (principal_type == "HUMAN") != (user_id is not None):
        raise IdentityAuthorityError("invalid_principal_user_binding")
    principal = Principal(
        principal_type=principal_type,
        identity_key=identity_key,
        user_id=user_id,
    )
    db.add(principal)
    db.commit()
    db.refresh(principal)
    return principal


def assign_feishu_identity(
    db,
    *,
    tenant_id: str,
    open_id: str,
    principal_id: UUID,
    audit_event_id: UUID,
) -> ExternalIdentity:
    principal = db.get(Principal, principal_id)
    if principal is None or principal.principal_type != "HUMAN":
        raise IdentityAuthorityError("human_principal_required")
    tenant = external_identity_fingerprint(f"tenant:{tenant_id}")
    subject = external_identity_fingerprint(f"subject:{open_id}")
    now = db.execute(select(func.clock_timestamp())).scalar_one()
    current = db.execute(
        select(ExternalIdentity)
        .where(
            ExternalIdentity.provider == "feishu",
            ExternalIdentity.tenant_fingerprint == tenant,
            ExternalIdentity.subject_fingerprint == subject,
            ExternalIdentity.valid_to.is_(None),
        )
        .with_for_update()
    ).scalar_one_or_none()
    version = 1
    if current is not None:
        if current.principal_id == principal_id:
            return current
        current.valid_to = now
        version = current.mapping_version + 1
        db.flush()
    mapping = ExternalIdentity(
        provider="feishu",
        tenant_fingerprint=tenant,
        subject_fingerprint=subject,
        principal_id=principal_id,
        mapping_version=version,
        valid_from=now,
        valid_to=None,
        audit_event_id=audit_event_id,
    )
    db.add(mapping)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise IdentityAuthorityError("identity_assignment_conflict") from exc
    db.refresh(mapping)
    return mapping
