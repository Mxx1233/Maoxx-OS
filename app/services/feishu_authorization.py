from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    reason: str


def authorize_feishu_message(
    *,
    tenant_key: str | None,
    sender_type: str | None,
    sender_open_id: str | None,
    chat_type: str | None,
    allowed_tenant_keys: frozenset[str],
    allowed_open_ids: frozenset[str],
    allowed_chat_types: frozenset[str],
) -> AuthorizationDecision:
    """Apply the fail-closed Feishu ingestion authorization policy."""

    if not tenant_key or tenant_key not in allowed_tenant_keys:
        return AuthorizationDecision(False, "tenant_not_allowed")

    if sender_type != "user":
        return AuthorizationDecision(False, "sender_type_not_allowed")

    if not sender_open_id or sender_open_id not in allowed_open_ids:
        return AuthorizationDecision(False, "sender_not_allowed")

    if not chat_type or chat_type not in allowed_chat_types:
        return AuthorizationDecision(False, "chat_type_not_allowed")

    return AuthorizationDecision(True, "allowed")


def identifier_fingerprint(value: str | None) -> str:
    """Return a non-reversible short identifier for safe correlation logs."""

    if not value:
        return "missing"

    return sha256(value.encode("utf-8")).hexdigest()[:12]
