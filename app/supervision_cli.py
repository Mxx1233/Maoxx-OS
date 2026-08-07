import argparse
import json
import subprocess
import sys
from dataclasses import dataclass

from app.config import settings
from app.db import SessionLocal
from app.services.approval_service import (
    ApprovalValidationError,
    create_approval_request,
)
from app.services.feishu_messaging import (
    build_feishu_client,
    send_structured_text,
)
from app.services.supervision_notifications import (
    NotificationValidationError,
    SupervisionNotification,
    format_notification,
)


class GitHubVerificationError(RuntimeError):
    pass


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


def verify_github_state(evidence: GitHubEvidence) -> None:
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
        response = _run_gh_json(
            [
                "api",
                f"repos/{evidence.repository}/commits/"
                f"{evidence.target_sha}/check-runs",
            ]
        )
        if not isinstance(response, dict):
            raise GitHubVerificationError("GitHub check response invalid")
        checks = [
            check
            for check in response.get("check_runs", [])
            if isinstance(check, dict)
            and check.get("name") == "CI / Quality Gate"
        ]
        if not checks:
            raise GitHubVerificationError("Quality Gate unavailable")
        checks.sort(
            key=lambda check: (
                str(check.get("started_at") or ""),
                int(check.get("id") or 0),
            ),
            reverse=True,
        )
        latest = checks[0]
        if latest.get("status") != "completed":
            raise GitHubVerificationError("Quality Gate is not completed")
        conclusion = latest.get("conclusion")
        if evidence.event_type == "ci_passed" and conclusion != "success":
            raise GitHubVerificationError("Quality Gate is not successful")
        if evidence.event_type == "ci_failed" and conclusion not in {
            "failure",
            "cancelled",
            "timed_out",
        }:
            raise GitHubVerificationError("Quality Gate failure not verified")


def _send(notification: SupervisionNotification) -> bool:
    if not settings.approval_configuration_valid:
        raise NotificationValidationError(
            "supervision/approval configuration is incomplete"
        )
    client = build_feishu_client(
        settings.feishu_app_id,
        settings.feishu_app_secret,
    )
    result = send_structured_text(
        client,
        chat_id=settings.feishu_supervision_chat_id.strip(),
        text=format_notification(notification),
        max_attempts=settings.feishu_reply_max_attempts,
        backoff_seconds=settings.feishu_reply_backoff_seconds,
    )
    return result.sent


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
        return 0 if _send(notification) else 1

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
    return 0 if _send(notification) else 1


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        exit_code = run(args)
    except (
        ApprovalValidationError,
        NotificationValidationError,
        GitHubVerificationError,
    ) as exc:
        print(f"supervision request rejected: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except Exception:
        print("supervision operation failed closed", file=sys.stderr)
        raise SystemExit(2) from None
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
