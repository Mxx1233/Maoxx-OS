import json
import subprocess
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

from app.supervision_cli import (
    GitHubEvidence,
    GitHubVerificationError,
    _send,
    _run_gh_paginated_check_runs,
    run,
    verify_github_state,
)
from app.services.feishu_delivery import DeliveryResult
from app.services.supervision_notifications import SupervisionNotification


SHA = "a" * 40


class SupervisionCliTests(unittest.TestCase):
    @staticmethod
    def _gate(
        *,
        status: str = "completed",
        conclusion: str | None = "success",
        name: str = "CI / Quality Gate",
        check_id: object = None,
    ) -> dict:
        return {
            "id": check_id,
            "name": name,
            "status": status,
            "conclusion": conclusion,
        }

    def _verify_gate_runs(
        self,
        check_runs: object,
        *,
        event_type: str = "ci_passed",
    ) -> None:
        with (
            patch("app.supervision_cli._run_gh_json") as run_gh,
            patch(
                "app.supervision_cli._run_gh_paginated_check_runs",
                return_value=check_runs,
            ),
        ):
            run_gh.side_effect = [
                {"sha": SHA},
                {"number": 14, "headRefOid": SHA, "state": "OPEN"},
            ]
            verify_github_state(
                GitHubEvidence(
                    event_type,
                    "Mxx1233/Maoxx-OS",
                    SHA,
                    14,
                )
            )

    @patch("app.supervision_cli._run_gh_paginated_check_runs")
    @patch("app.supervision_cli._run_gh_json")
    def test_exact_github_quality_gate_success(
        self,
        run_gh: MagicMock,
        paginated_checks: MagicMock,
    ) -> None:
        run_gh.side_effect = [
            {"sha": SHA},
            {"number": 14, "headRefOid": SHA, "state": "OPEN"},
        ]
        paginated_checks.return_value = [self._gate(check_id="arbitrary")]
        verify_github_state(
            GitHubEvidence("ci_passed", "Mxx1233/Maoxx-OS", SHA, 14)
        )
        self.assertEqual(
            paginated_checks.call_args.args[0],
            "repos/Mxx1233/Maoxx-OS/commits/"
            f"{SHA}/check-runs?filter=latest&per_page=100",
        )

    @patch("app.supervision_cli.subprocess.run")
    def test_paginated_query_uses_safe_latest_filter_json_lines(
        self,
        subprocess_run: MagicMock,
    ) -> None:
        subprocess_run.return_value = SimpleNamespace(
            stdout=(
                '{"name":"CI / Other","status":"completed"}\n'
                '{"name":"CI / Quality Gate","status":"completed",'
                '"conclusion":"success"}\n'
            )
        )
        checks = _run_gh_paginated_check_runs(
            "repos/Mxx1233/Maoxx-OS/commits/"
            f"{SHA}/check-runs?filter=latest&per_page=100"
        )
        self.assertEqual(len(checks), 2)
        subprocess_run.assert_called_once_with(
            [
                "gh",
                "api",
                "--paginate",
                "repos/Mxx1233/Maoxx-OS/commits/"
                f"{SHA}/check-runs?filter=latest&per_page=100",
                "--jq",
                ".check_runs[] | @json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    @patch("app.supervision_cli._run_gh_json")
    def test_github_mismatch_fails_closed(self, run_gh: MagicMock) -> None:
        run_gh.return_value = {"sha": "b" * 40}
        with self.assertRaises(GitHubVerificationError):
            verify_github_state(
                GitHubEvidence(
                    "staging_passed",
                    "Mxx1233/Maoxx-OS",
                    SHA,
                    None,
                )
            )

    @patch("app.supervision_cli._run_gh_json")
    def test_github_verification_rejects_non_exact_sha(
        self,
        run_gh: MagicMock,
    ) -> None:
        with self.assertRaises(GitHubVerificationError):
            verify_github_state(
                GitHubEvidence(
                    "ci_passed",
                    "Mxx1233/Maoxx-OS",
                    "A" * 40,
                    14,
                )
            )
        run_gh.assert_not_called()

    def test_non_success_gate_never_satisfies_ci_passed(self) -> None:
        for status, conclusion in (
            ("pending", None),
            ("queued", None),
            ("in_progress", None),
            ("completed", "failure"),
            ("completed", "cancelled"),
            ("completed", "timed_out"),
            ("completed", "neutral"),
            ("completed", "skipped"),
        ):
            with self.subTest(status=status):
                with self.assertRaises(GitHubVerificationError):
                    self._verify_gate_runs(
                        [
                            self._gate(
                                status=status,
                                conclusion=conclusion,
                            )
                        ]
                    )

    def test_ci_failed_requires_completed_failure_conclusion(self) -> None:
        for conclusion in ("failure", "cancelled", "timed_out"):
            with self.subTest(conclusion=conclusion):
                self._verify_gate_runs(
                    [self._gate(conclusion=conclusion)],
                    event_type="ci_failed",
                )
        with self.assertRaises(GitHubVerificationError):
            self._verify_gate_runs(
                [
                    self._gate(
                        status="in_progress",
                        conclusion=None,
                    )
                ],
                event_type="ci_failed",
            )

    def test_exact_quality_gate_is_required_and_unique(self) -> None:
        cases = (
            [],
            [self._gate(name="CI / Quality Gate Extra")],
            [
                self._gate(check_id=999),
                self._gate(check_id=-7),
            ],
        )
        for check_runs in cases:
            with self.subTest(check_runs=check_runs):
                with self.assertRaises(GitHubVerificationError):
                    self._verify_gate_runs(check_runs)

    def test_response_order_and_ids_do_not_determine_freshness(self) -> None:
        exact_gate = self._gate(check_id=-900)
        unrelated = self._gate(
            name="CI / Other",
            check_id=999999999,
        )
        for check_runs in (
            [exact_gate, unrelated],
            [unrelated, exact_gate],
        ):
            with self.subTest(check_runs=check_runs):
                self._verify_gate_runs(check_runs)

    @patch("app.supervision_cli.subprocess.run")
    def test_later_page_quality_gate_causes_ambiguity(
        self,
        subprocess_run: MagicMock,
    ) -> None:
        gates = [self._gate(check_id=100), self._gate(check_id=1)]
        for ordered_gates in (gates, list(reversed(gates))):
            with self.subTest(ordered_gates=ordered_gates):
                subprocess_run.return_value = SimpleNamespace(
                    stdout="\n".join(
                        json.dumps(gate) for gate in ordered_gates
                    )
                )
                checks = _run_gh_paginated_check_runs(
                    "repos/Mxx1233/Maoxx-OS/commits/"
                    f"{SHA}/check-runs?filter=latest&per_page=1"
                )
                with self.assertRaises(GitHubVerificationError):
                    self._verify_gate_runs(checks)

    @patch("app.supervision_cli.subprocess.run")
    def test_malformed_paginated_json_fails_closed(
        self,
        subprocess_run: MagicMock,
    ) -> None:
        for malformed in ("not-json", '"not-a-check"'):
            with self.subTest(malformed=malformed):
                subprocess_run.return_value = SimpleNamespace(stdout=malformed)
                with self.assertRaises(GitHubVerificationError):
                    _run_gh_paginated_check_runs(
                        "repos/Mxx1233/Maoxx-OS/commits/"
                        f"{SHA}/check-runs?filter=latest&per_page=100"
                    )

        subprocess_run.side_effect = subprocess.CalledProcessError(
            1,
            ["gh", "api", "--paginate"],
        )
        with self.assertRaises(GitHubVerificationError):
            _run_gh_paginated_check_runs(
                "repos/Mxx1233/Maoxx-OS/commits/"
                f"{SHA}/check-runs?filter=latest&per_page=100"
            )

    def test_approval_is_persisted_before_message_send(self) -> None:
        events: list[str] = []
        request = SimpleNamespace(
            id=UUID("11111111-1111-4111-8111-111111111111"),
            repository="Mxx1233/Maoxx-OS",
            target_sha=SHA,
            pull_request_number=14,
            target_environment="production",
            action_code="production_deploy",
            expires_at=datetime(2026, 8, 7, 12, tzinfo=UTC),
        )
        args = SimpleNamespace(
            command="request-approval",
            action="production_deploy",
            repository="Mxx1233/Maoxx-OS",
            sha=SHA,
            pr_number=14,
            environment="production",
            idempotency_key="deploy-14-a",
            verify_github=False,
        )
        session_local = MagicMock()

        def persist(*_args, **_kwargs):
            events.append("persisted")
            return request

        def send(_notification):
            events.append("sent")
            return DeliveryResult(sent=True, attempts=1)

        with (
            patch("app.supervision_cli.SessionLocal", session_local),
            patch(
                "app.supervision_cli.create_approval_request",
                side_effect=persist,
            ),
            patch("app.supervision_cli._send", side_effect=send),
        ):
            self.assertEqual(run(args), 0)
        self.assertEqual(events, ["persisted", "sent"])

    def test_approval_message_id_persisted_after_send(self) -> None:
        # H-8: after a successful card send the platform message id must be
        # persisted on the approval request so the card can be rebuilt from
        # the authoritative database instead of chat history.
        request_id = UUID("22222222-2222-4222-8222-222222222222")
        request = SimpleNamespace(
            id=request_id,
            repository="Mxx1233/Maoxx-OS",
            target_sha=SHA,
            pull_request_number=14,
            target_environment="production",
            action_code="production_deploy",
            expires_at=datetime(2026, 8, 7, 12, tzinfo=UTC),
        )
        args = SimpleNamespace(
            command="request-approval",
            action="production_deploy",
            repository="Mxx1233/Maoxx-OS",
            sha=SHA,
            pr_number=14,
            environment="production",
            idempotency_key="deploy-14-b",
            verify_github=False,
        )
        persisted = SimpleNamespace(card_message_id=None)
        session = MagicMock()

        def session_context():
            session.get.return_value = persisted
            return session

        session_local = MagicMock()
        session_local.return_value.__enter__.side_effect = session_context
        session_local.return_value.__exit__.return_value = False

        with (
            patch("app.supervision_cli.SessionLocal", session_local),
            patch(
                "app.supervision_cli.create_approval_request",
                return_value=request,
            ),
            patch(
                "app.supervision_cli._send",
                return_value=DeliveryResult(
                    sent=True, attempts=1, message_id="om_persisted_card"
                ),
            ),
        ):
            self.assertEqual(run(args), 0)
        self.assertEqual(persisted.card_message_id, "om_persisted_card")
        session.commit.assert_called_once()

    def test_approval_message_id_not_persisted_when_send_failed(self) -> None:
        request_id = UUID("33333333-3333-4333-8333-333333333333")
        request = SimpleNamespace(
            id=request_id,
            repository="Mxx1233/Maoxx-OS",
            target_sha=SHA,
            pull_request_number=14,
            target_environment="production",
            action_code="production_deploy",
            expires_at=datetime(2026, 8, 7, 12, tzinfo=UTC),
        )
        args = SimpleNamespace(
            command="request-approval",
            action="production_deploy",
            repository="Mxx1233/Maoxx-OS",
            sha=SHA,
            pr_number=14,
            environment="production",
            idempotency_key="deploy-14-c",
            verify_github=False,
        )
        session_local = MagicMock()

        with (
            patch("app.supervision_cli.SessionLocal", session_local),
            patch(
                "app.supervision_cli.create_approval_request",
                return_value=request,
            ),
            patch(
                "app.supervision_cli._send",
                return_value=DeliveryResult(
                    sent=False, attempts=3, failure_reason="permanent_error"
                ),
            ),
        ):
            self.assertEqual(run(args), 1)
        session_local.assert_called_once()  # only the create session
        session_local.return_value.__enter__.return_value.commit.assert_not_called()

    def test_approval_notification_uses_interactive_card_as_primary(
        self,
    ) -> None:
        approval = SupervisionNotification(
            event_type="approval_required",
            repository="Mxx1233/Maoxx-OS",
            target_sha=SHA,
            pull_request_number=14,
            target_environment="production",
            approval_request_id=UUID("11111111-1111-4111-8111-111111111111"),
            action_code="production_deploy",
            expires_at=datetime(2026, 8, 7, 12, tzinfo=UTC),
        )
        with (
            patch("app.supervision_cli.build_feishu_client"),
            patch("app.supervision_cli.send_interactive_card") as send_card,
            patch("app.supervision_cli.send_structured_text") as send_text,
            patch(
                "app.supervision_cli.settings",
                SimpleNamespace(
                    approval_configuration_valid=True,
                    feishu_app_id="fake-app-id",
                    feishu_app_secret="fake-app-secret",
                    feishu_supervision_chat_id="fake-chat-id",
                    feishu_reply_max_attempts=1,
                    feishu_reply_backoff_seconds=0,
                ),
            ),
        ):
            send_card.return_value = SimpleNamespace(sent=True)
            self.assertTrue(_send(approval))
        send_card.assert_called_once()
        send_text.assert_not_called()
        self.assertEqual(
            send_card.call_args.kwargs["card"]["elements"][-1]["tag"],
            "action",
        )


if __name__ == "__main__":
    unittest.main()
