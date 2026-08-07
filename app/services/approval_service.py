import re
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import ApprovalDecision, ApprovalRequest
from app.services.feishu_authorization import authorize_feishu_message


APPROVAL_ACTIONS = frozenset(
    {"development_plan", "merge_pr", "production_deploy"}
)
TARGET_ENVIRONMENTS = frozenset({"development", "staging", "production"})
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$"
)
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
COMMAND_PATTERN = re.compile(
    r"^(批准|拒绝)[ \t]+"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})$"
)


class ApprovalValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ApprovalCommand:
    decision_code: str
    request_id: UUID


@dataclass(frozen=True)
class ApprovalCommandParse:
    approval_like: bool
    command: ApprovalCommand | None = None


@dataclass(frozen=True)
class ApprovalOutcome:
    code: str
    decision_code: str | None = None


def parse_approval_command(text: str) -> ApprovalCommandParse:
    normalized = text.strip()
    if not normalized.startswith(("批准", "拒绝")):
        return ApprovalCommandParse(False)

    match = COMMAND_PATTERN.fullmatch(normalized)
    if not match:
        return ApprovalCommandParse(True)

    request_text = match.group(2)
    try:
        request_id = UUID(request_text)
    except ValueError:
        return ApprovalCommandParse(True)
    if str(request_id) != request_text:
        return ApprovalCommandParse(True)

    decision_code = "approved" if match.group(1) == "批准" else "rejected"
    return ApprovalCommandParse(
        True,
        ApprovalCommand(decision_code, request_id),
    )


def approval_identity_fingerprint(open_id: str) -> str:
    return sha256(open_id.encode("utf-8")).hexdigest()


def validate_approval_request_fields(
    *,
    action_code: str,
    repository: str,
    pull_request_number: int | None,
    target_sha: str,
    target_environment: str,
    idempotency_key: str,
    ttl_seconds: int,
) -> None:
    if action_code not in APPROVAL_ACTIONS:
        raise ApprovalValidationError("unsupported action_code")
    if not REPOSITORY_PATTERN.fullmatch(repository) or repository.endswith(
        ".git"
    ):
        raise ApprovalValidationError("invalid repository")
    if pull_request_number is not None and pull_request_number < 1:
        raise ApprovalValidationError("invalid pull_request_number")
    if not SHA_PATTERN.fullmatch(target_sha):
        raise ApprovalValidationError("invalid target_sha")
    if target_environment not in TARGET_ENVIRONMENTS:
        raise ApprovalValidationError("invalid target_environment")
    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
        raise ApprovalValidationError("invalid idempotency_key")
    if ttl_seconds < 1 or ttl_seconds > 86400:
        raise ApprovalValidationError("invalid ttl_seconds")
    if action_code == "merge_pr" and pull_request_number is None:
        raise ApprovalValidationError("merge_pr requires pull_request_number")
    expected_environment = {
        "development_plan": "development",
        "merge_pr": "staging",
        "production_deploy": "production",
    }[action_code]
    if target_environment != expected_environment:
        raise ApprovalValidationError("action/environment mismatch")


def create_approval_request(
    db: Session,
    *,
    user_id: UUID,
    action_code: str,
    repository: str,
    pull_request_number: int | None,
    target_sha: str,
    target_environment: str,
    idempotency_key: str,
    ttl_seconds: int,
) -> ApprovalRequest:
    validate_approval_request_fields(
        action_code=action_code,
        repository=repository,
        pull_request_number=pull_request_number,
        target_sha=target_sha,
        target_environment=target_environment,
        idempotency_key=idempotency_key,
        ttl_seconds=ttl_seconds,
    )
    existing = db.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.idempotency_key == idempotency_key
        )
    ).scalar_one_or_none()
    expected = (
        user_id,
        action_code,
        repository,
        pull_request_number,
        target_sha,
        target_environment,
    )

    def require_matching(existing_request: ApprovalRequest) -> ApprovalRequest:
        actual = (
            existing_request.user_id,
            existing_request.action_code,
            existing_request.repository,
            existing_request.pull_request_number,
            existing_request.target_sha,
            existing_request.target_environment,
        )
        if actual != expected:
            raise ApprovalValidationError("idempotency_key conflict")
        return existing_request

    if existing is not None:
        return require_matching(existing)

    database_now = db.execute(select(func.current_timestamp())).scalar_one()
    request = ApprovalRequest(
        user_id=user_id,
        action_code=action_code,
        repository=repository,
        pull_request_number=pull_request_number,
        target_sha=target_sha,
        target_environment=target_environment,
        idempotency_key=idempotency_key,
        requested_at=database_now,
        expires_at=database_now + timedelta(seconds=ttl_seconds),
    )
    db.add(request)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raced_request = db.execute(
            select(ApprovalRequest).where(
                ApprovalRequest.idempotency_key == idempotency_key
            )
        ).scalar_one_or_none()
        if raced_request is None:
            raise
        return require_matching(raced_request)
    db.refresh(request)
    return request


def _matching_duplicate(
    decision: ApprovalDecision,
    *,
    command: ApprovalCommand,
    actor_user_id: UUID,
    actor_fingerprint: str,
) -> bool:
    return (
        decision.request_id == command.request_id
        and decision.decision_code == command.decision_code
        and decision.actor_user_id == actor_user_id
        and decision.approver_identity_fingerprint == actor_fingerprint
    )


def record_approval_decision(
    db: Session,
    *,
    command: ApprovalCommand,
    feishu_event_id: str,
    actor_user_id: UUID,
    actor_open_id: str,
) -> ApprovalOutcome:
    if not feishu_event_id or len(feishu_event_id) > 200:
        return ApprovalOutcome("invalid_event")

    actor_fingerprint = approval_identity_fingerprint(actor_open_id)
    try:
        duplicate_event = db.execute(
            select(ApprovalDecision).where(
                ApprovalDecision.feishu_event_id == feishu_event_id
            )
        ).scalar_one_or_none()
        if duplicate_event is not None:
            if _matching_duplicate(
                duplicate_event,
                command=command,
                actor_user_id=actor_user_id,
                actor_fingerprint=actor_fingerprint,
            ):
                return ApprovalOutcome("duplicate", command.decision_code)
            return ApprovalOutcome("event_conflict")

        request = db.execute(
            select(ApprovalRequest)
            .where(ApprovalRequest.id == command.request_id)
            .with_for_update()
        ).scalar_one_or_none()
        if request is None:
            return ApprovalOutcome("unknown_request")

        database_now = db.execute(
            select(func.current_timestamp())
        ).scalar_one()
        if database_now >= request.expires_at:
            return ApprovalOutcome("expired")

        existing_decision = db.execute(
            select(ApprovalDecision).where(
                ApprovalDecision.request_id == command.request_id
            )
        ).scalar_one_or_none()
        if existing_decision is not None:
            return ApprovalOutcome("already_decided")

        db.add(
            ApprovalDecision(
                request_id=command.request_id,
                decision_code=command.decision_code,
                actor_user_id=actor_user_id,
                approver_identity_fingerprint=actor_fingerprint,
                feishu_event_id=feishu_event_id,
                decided_at=database_now,
            )
        )
        db.commit()
        return ApprovalOutcome("recorded", command.decision_code)
    except IntegrityError:
        db.rollback()
        return ApprovalOutcome("conflict")
    except SQLAlchemyError:
        db.rollback()
        return ApprovalOutcome("unavailable")


def process_approval_message(
    db: Session,
    *,
    text: str,
    tenant_key: str | None,
    sender_type: str | None,
    sender_open_id: str | None,
    chat_type: str | None,
    chat_id: str | None,
    feishu_event_id: str,
    actor_user_id: UUID,
    allowed_tenant_keys: frozenset[str],
    allowed_open_ids: frozenset[str],
    allowed_chat_types: frozenset[str],
    approver_open_ids: frozenset[str],
    supervision_chat_id: str,
) -> ApprovalOutcome | None:
    parsed = parse_approval_command(text)
    if not parsed.approval_like:
        return None
    if parsed.command is None:
        return ApprovalOutcome("malformed")

    authorization = authorize_feishu_message(
        tenant_key=tenant_key,
        sender_type=sender_type,
        sender_open_id=sender_open_id,
        chat_type=chat_type,
        allowed_tenant_keys=allowed_tenant_keys,
        allowed_open_ids=allowed_open_ids,
        allowed_chat_types=allowed_chat_types,
    )
    if not authorization.allowed:
        return ApprovalOutcome("unauthorized")
    if (
        not sender_open_id
        or sender_open_id not in approver_open_ids
        or not supervision_chat_id
        or chat_id != supervision_chat_id
    ):
        return ApprovalOutcome("unauthorized")

    return record_approval_decision(
        db,
        command=parsed.command,
        feishu_event_id=feishu_event_id,
        actor_user_id=actor_user_id,
        actor_open_id=sender_open_id,
    )


def approval_outcome_reply(outcome: ApprovalOutcome) -> str:
    return {
        "malformed": (
            "审批命令格式无效。请使用：批准 <request-uuid> 或 "
            "拒绝 <request-uuid>。"
        ),
        "unauthorized": "无权执行该审批。",
        "invalid_event": "审批事件无效，未记录决定。",
        "unknown_request": "审批请求不存在或不可用。",
        "expired": "审批请求已过期，未记录决定。",
        "already_decided": "审批请求已经处理，未重复记录。",
        "event_conflict": "审批事件冲突，未记录决定。",
        "conflict": "审批请求发生冲突，未记录决定。",
        "unavailable": "审批服务暂时不可用，未记录决定。",
        "duplicate": "该审批事件已处理。",
        "recorded": (
            "审批决定已记录：批准。"
            if outcome.decision_code == "approved"
            else "审批决定已记录：拒绝。"
        ),
    }[outcome.code]
