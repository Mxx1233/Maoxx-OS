import unittest
from datetime import UTC, datetime
from uuid import UUID

from app.services.supervision_notifications import (
    NotificationValidationError,
    SupervisionNotification,
    format_notification,
    notification_from_fields,
)


SHA = "a" * 40
REQUEST_ID = UUID("11111111-1111-4111-8111-111111111111")


class SupervisionNotificationTests(unittest.TestCase):
    def test_formats_every_fixed_event(self) -> None:
        cases = {
            "pr_created": {"pull_request_number": 14},
            "ci_passed": {"pull_request_number": 14},
            "ci_failed": {"pull_request_number": 14},
            "staging_started": {"target_environment": "staging"},
            "staging_passed": {"target_environment": "staging"},
            "staging_failed": {"target_environment": "staging"},
            "approval_required": {
                "pull_request_number": 14,
                "target_environment": "production",
                "approval_request_id": REQUEST_ID,
                "action_code": "production_deploy",
                "expires_at": datetime(2026, 8, 7, 12, tzinfo=UTC),
            },
        }
        for event_type, fields in cases.items():
            with self.subTest(event_type=event_type):
                message = format_notification(
                    SupervisionNotification(
                        event_type=event_type,
                        repository="Mxx1233/Maoxx-OS",
                        target_sha=SHA,
                        **fields,
                    )
                )
                self.assertIn(event_type, message)
                self.assertIn(SHA, message)
                self.assertNotIn("secret", message.lower())

    def test_rejects_unknown_event_invalid_sha_and_unsafe_fields(self) -> None:
        with self.assertRaises(NotificationValidationError):
            SupervisionNotification(
                event_type="arbitrary_event",
                repository="Mxx1233/Maoxx-OS",
                target_sha=SHA,
            ).validate()
        with self.assertRaises(NotificationValidationError):
            SupervisionNotification(
                event_type="pr_created",
                repository="Mxx1233/Maoxx-OS",
                target_sha="not-a-sha",
                pull_request_number=14,
            ).validate()
        with self.assertRaises(NotificationValidationError):
            notification_from_fields(
                {
                    "event_type": "pr_created",
                    "repository": "Mxx1233/Maoxx-OS",
                    "target_sha": SHA,
                    "pull_request_number": 14,
                    "raw_logs": "must not be forwarded",
                }
            )

    def test_direct_approval_message_requires_persisted_request_fields(
        self,
    ) -> None:
        with self.assertRaises(NotificationValidationError):
            SupervisionNotification(
                event_type="approval_required",
                repository="Mxx1233/Maoxx-OS",
                target_sha=SHA,
            ).validate()

    def test_event_specific_fields_are_strict(self) -> None:
        with self.assertRaises(NotificationValidationError):
            SupervisionNotification(
                event_type="ci_passed",
                repository="Mxx1233/Maoxx-OS",
                target_sha=SHA,
                pull_request_number=14,
                target_environment="production",
            ).validate()
        with self.assertRaises(NotificationValidationError):
            SupervisionNotification(
                event_type="approval_required",
                repository="Mxx1233/Maoxx-OS",
                target_sha=SHA,
                pull_request_number=14,
                target_environment="production",
                approval_request_id=REQUEST_ID,
                action_code="merge_pr",
                expires_at=datetime(2026, 8, 7, 12, tzinfo=UTC),
            ).validate()


if __name__ == "__main__":
    unittest.main()
