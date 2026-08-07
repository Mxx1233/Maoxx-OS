import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

from app.supervision_cli import (
    GitHubEvidence,
    GitHubVerificationError,
    run,
    verify_github_state,
)


SHA = "a" * 40


class SupervisionCliTests(unittest.TestCase):
    @patch("app.supervision_cli._run_gh_json")
    def test_exact_github_quality_gate_success(
        self, run_gh: MagicMock
    ) -> None:
        run_gh.side_effect = [
            {"sha": SHA},
            {"number": 14, "headRefOid": SHA, "state": "OPEN"},
            {
                "check_runs": [
                    {
                        "id": 1,
                        "name": "CI / Quality Gate",
                        "status": "completed",
                        "conclusion": "success",
                        "started_at": "2026-08-07T10:00:00Z",
                    }
                ]
            },
        ]
        verify_github_state(
            GitHubEvidence("ci_passed", "Mxx1233/Maoxx-OS", SHA, 14)
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
    def test_newer_pending_gate_rejects_old_success(
        self,
        run_gh: MagicMock,
    ) -> None:
        run_gh.side_effect = [
            {"sha": SHA},
            {"number": 14, "headRefOid": SHA, "state": "OPEN"},
            {
                "check_runs": [
                    {
                        "id": 1,
                        "name": "CI / Quality Gate",
                        "status": "completed",
                        "conclusion": "success",
                        "started_at": "2026-08-07T10:00:00Z",
                    },
                    {
                        "id": 2,
                        "name": "CI / Quality Gate",
                        "status": "in_progress",
                        "conclusion": None,
                        "started_at": "2026-08-07T11:00:00Z",
                    },
                ]
            },
        ]
        with self.assertRaises(GitHubVerificationError):
            verify_github_state(
                GitHubEvidence(
                    "ci_passed",
                    "Mxx1233/Maoxx-OS",
                    SHA,
                    14,
                )
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
            return True

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


if __name__ == "__main__":
    unittest.main()
