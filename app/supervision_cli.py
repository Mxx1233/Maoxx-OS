import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from app.config import settings
from app.db import SessionLocal
from app.models import ApprovalRequest
from app.services.approval_service import (
    ApprovalValidationError,
    create_approval_request,
)
from app.services.backup_authority import (
    BackupAuthorityError,
    ProductionBackupAuthority,
)
from app.services.feishu_delivery import DeliveryResult
from app.services.feishu_messaging import (
    build_feishu_client,
    send_interactive_card,
    send_structured_text,
)
from app.services.identity_authority import (
    IdentityAuthorityError,
    assign_feishu_identity,
    provision_principal,
)
from app.services.approval_cards import build_approval_card
from app.services.supervision_notifications import (
    NotificationValidationError,
    SupervisionNotification,
    format_notification,
)


class GitHubVerificationError(RuntimeError):
    pass


QUALITY_GATE_NAME = "CI / Quality Gate"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class GitHubEvidence:
    event_type: str
    repository: str
    target_sha: str
    pull_request_number: int | None


def _run_gh_json(arguments: list[str]) -> object:
    try:
        result = subprocess.run(
            ["gh", *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        json.JSONDecodeError,
    ) as exc:
        raise GitHubVerificationError(
            "GitHub verification unavailable"
        ) from exc


def _run_gh_paginated_check_runs(endpoint: str) -> list[dict]:
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                "--paginate",
                endpoint,
                "--jq",
                ".check_runs[] | @json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        check_runs = [json.loads(line) for line in result.stdout.splitlines()]
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        json.JSONDecodeError,
    ) as exc:
        raise GitHubVerificationError(
            "GitHub paginated check verification unavailable"
        ) from exc
    if any(not isinstance(check, dict) for check in check_runs):
        raise GitHubVerificationError("GitHub check response invalid")
    return check_runs


def verify_github_state(evidence: GitHubEvidence) -> None:
    if not SHA_PATTERN.fullmatch(evidence.target_sha):
        raise GitHubVerificationError("GitHub target SHA invalid")

    commit = _run_gh_json(
        ["api", f"repos/{evidence.repository}/commits/{evidence.target_sha}"]
    )
    if (
        not isinstance(commit, dict)
        or commit.get("sha") != evidence.target_sha
    ):
        raise GitHubVerificationError("GitHub commit SHA mismatch")

    if evidence.pull_request_number is not None:
        pull_request = _run_gh_json(
            [
                "pr",
                "view",
                str(evidence.pull_request_number),
                "--repo",
                evidence.repository,
                "--json",
                "number,headRefOid,state",
            ]
        )
        if (
            not isinstance(pull_request, dict)
            or pull_request.get("number") != evidence.pull_request_number
        ):
            raise GitHubVerificationError("GitHub PR mismatch")
        if (
            evidence.event_type
            in {
                "pr_created",
                "ci_passed",
                "ci_failed",
                "merge_pr",
            }
            and pull_request.get("headRefOid") != evidence.target_sha
        ):
            raise GitHubVerificationError("GitHub PR head SHA mismatch")

    if evidence.event_type in {"ci_passed", "ci_failed"}:
        check_runs = _run_gh_paginated_check_runs(
            f"repos/{evidence.repository}/commits/"
            f"{evidence.target_sha}/check-runs?filter=latest&per_page=100"
        )
        if any(not isinstance(check, dict) for check in check_runs):
            raise GitHubVerificationError("GitHub check response invalid")
        checks = [
            check
            for check in check_runs
            if check.get("name") == QUALITY_GATE_NAME
        ]

        if len(checks) == 0:
            raise GitHubVerificationError("Quality Gate unavailable")
        if len(checks) != 1:
            raise GitHubVerificationError("Quality Gate is ambiguous")

        quality_gate = checks[0]
        if quality_gate.get("status") != "completed":
            raise GitHubVerificationError("Quality Gate is not completed")
        conclusion = quality_gate.get("conclusion")
        if evidence.event_type == "ci_passed" and conclusion != "success":
            raise GitHubVerificationError("Quality Gate is not successful")
        if evidence.event_type == "ci_failed" and conclusion not in {
            "failure",
            "cancelled",
            "timed_out",
        }:
            raise GitHubVerificationError("Quality Gate failure not verified")


def _send(notification: SupervisionNotification) -> DeliveryResult:
    if not settings.approval_configuration_valid:
        raise NotificationValidationError(
            "supervision/approval configuration is incomplete"
        )
    client = build_feishu_client(
        settings.feishu_app_id,
        settings.feishu_app_secret,
    )
    common = {
        "chat_id": settings.feishu_supervision_chat_id.strip(),
        "max_attempts": settings.feishu_reply_max_attempts,
        "backoff_seconds": settings.feishu_reply_backoff_seconds,
    }
    if notification.event_type == "approval_required":
        result = send_interactive_card(
            client,
            card=build_approval_card(notification),
            **common,
        )
    else:
        result = send_structured_text(
            client,
            text=format_notification(notification),
            **common,
        )
    return result


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--pr-number", type=int)
    parser.add_argument("--verify-github", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send fixed Phase 1D-E supervision messages."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    notify = subparsers.add_parser("notify")
    notify.add_argument(
        "--event",
        required=True,
        choices=(
            "pr_created",
            "ci_passed",
            "ci_failed",
            "staging_started",
            "staging_passed",
            "staging_failed",
        ),
    )
    _add_common_arguments(notify)
    notify.add_argument(
        "--environment",
        choices=("development", "staging", "production"),
    )

    approval = subparsers.add_parser("request-approval")
    approval.add_argument(
        "--action",
        required=True,
        choices=("development_plan", "merge_pr", "production_deploy"),
    )
    _add_common_arguments(approval)
    approval.add_argument(
        "--environment",
        required=True,
        choices=("development", "staging", "production"),
    )
    approval.add_argument("--idempotency-key", required=True)

    principal = subparsers.add_parser(
        "provision-principal", help="Provision a typed principal."
    )
    principal.add_argument(
        "--type",
        required=True,
        choices=("HUMAN", "SERVICE", "EXECUTOR"),
        dest="principal_type",
    )
    principal.add_argument("--identity-key", required=True)
    principal.add_argument("--user-id", type=UUID)

    identity = subparsers.add_parser(
        "assign-feishu-identity",
        help="Map a Feishu tenant/open id to a HUMAN principal.",
    )
    identity.add_argument("--tenant-id", required=True)
    identity.add_argument("--open-id", required=True)
    identity.add_argument("--principal-id", type=UUID, required=True)

    backup = subparsers.add_parser(
        "create-backup",
        help="Create and verify a deployment-bound production backup.",
    )
    backup.add_argument("--deployment-id", type=UUID, required=True)
    backup.add_argument(
        "--backup-root",
        type=Path,
        default=Path("/opt/maoxx-os/backups"),
    )
    backup.add_argument("--pg-service", default="maoxx_production")
    return parser


def _github_evidence_from_args(args: argparse.Namespace) -> GitHubEvidence:
    event_type = args.event if args.command == "notify" else args.action
    return GitHubEvidence(
        event_type=event_type,
        repository=args.repository,
        target_sha=args.sha,
        pull_request_number=args.pr_number,
    )


def run(args: argparse.Namespace) -> int:
    if args.command == "notify":
        notification = SupervisionNotification(
            event_type=args.event,
            repository=args.repository,
            target_sha=args.sha,
            pull_request_number=args.pr_number,
            target_environment=args.environment,
        )
        notification.validate()
        if args.verify_github:
            verify_github_state(_github_evidence_from_args(args))
        return 0 if _send(notification).sent else 1

    if args.command == "provision-principal":
        with SessionLocal() as db:
            principal = provision_principal(
                db,
                principal_type=args.principal_type,
                identity_key=args.identity_key,
                user_id=args.user_id,
            )
        print(f"principal_id={principal.id}")
        return 0

    if args.command == "assign-feishu-identity":
        with SessionLocal() as db:
            mapping = assign_feishu_identity(
                db,
                tenant_id=args.tenant_id,
                open_id=args.open_id,
                principal_id=args.principal_id,
                audit_event_id=uuid4(),
            )
        print(f"mapping_id={mapping.id} version={mapping.mapping_version}")
        return 0

    if args.command == "create-backup":
        authority = ProductionBackupAuthority(
            session_factory=SessionLocal,
            backup_root=args.backup_root,
            pg_service=args.pg_service,
        )
        evidence = authority.create_and_verify(args.deployment_id)
        print(
            f"backup_evidence_key={evidence.evidence_key} "
            f"status={evidence.status_code}"
        )
        return 0

    if args.verify_github:
        verify_github_state(_github_evidence_from_args(args))
    with SessionLocal() as db:
        request = create_approval_request(
            db,
            user_id=settings.default_user_id,
            action_code=args.action,
            repository=args.repository,
            pull_request_number=args.pr_number,
            target_sha=args.sha,
            target_environment=args.environment,
            idempotency_key=args.idempotency_key,
            ttl_seconds=settings.feishu_approval_ttl_seconds,
        )
    notification = SupervisionNotification(
        event_type="approval_required",
        repository=request.repository,
        target_sha=request.target_sha,
        pull_request_number=request.pull_request_number,
        target_environment=request.target_environment,
        approval_request_id=request.id,
        action_code=request.action_code,
        expires_at=request.expires_at,
    )
    notification.validate()
    result = _send(notification)
    if result.sent and result.message_id:
        with SessionLocal() as db:
            persisted = db.get(ApprovalRequest, request.id)
            if persisted is not None:
                persisted.card_message_id = result.message_id
                db.commit()
    return 0 if result.sent else 1


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        exit_code = run(args)
    except (
        ApprovalValidationError,
        NotificationValidationError,
        GitHubVerificationError,
        IdentityAuthorityError,
        BackupAuthorityError,
    ) as exc:
        print(f"supervision request rejected: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except Exception:
        print("supervision operation failed closed", file=sys.stderr)
        raise SystemExit(2) from None
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
