import unittest
from datetime import UTC, datetime
from uuid import UUID

from app.services.approval_cards import (
    ApprovalCardAction,
    build_approval_card,
    build_card_callback_response,
    parse_approval_card_action,
)
from app.services.approval_service import ApprovalOutcome
from app.services.supervision_notifications import SupervisionNotification


REQUEST_ID = UUID("11111111-1111-4111-8111-111111111111")


def notification() -> SupervisionNotification:
    return SupervisionNotification(
        event_type="approval_required",
        repository="Mxx1233/Maoxx-OS",
        target_sha="a" * 40,
        pull_request_number=14,
        target_environment="production",
        approval_request_id=REQUEST_ID,
        action_code="production_deploy",
        expires_at=datetime(2026, 8, 8, 12, tzinfo=UTC),
    )


class ApprovalCardTests(unittest.TestCase):
    def test_pending_card_has_exact_three_minimal_actions(self) -> None:
        card = build_approval_card(notification())
        action_element = card["elements"][-1]
        actions = action_element["actions"]
        self.assertEqual(
            [action["text"]["content"] for action in actions],
            ["批准", "拒绝", "等一下"],
        )
        self.assertEqual(
            [action["value"]["action"] for action in actions],
            ["approve", "reject", "wait"],
        )
        for action in actions:
            self.assertEqual(
                set(action["value"]),
                {"action", "request_id"},
            )
            self.assertEqual(action["value"]["request_id"], str(REQUEST_ID))
        serialized = str(card)
        self.assertIn("a" * 40, serialized)
        self.assertIn("Mxx1233/Maoxx-OS", serialized)
        self.assertNotIn("shell", serialized.lower())
        self.assertNotIn("secret", serialized.lower())

    def test_final_and_waiting_card_states_are_unambiguous(self) -> None:
        approved = build_approval_card(notification(), state="approved")
        rejected = build_approval_card(notification(), state="rejected")
        waiting = build_approval_card(notification(), state="waiting")
        self.assertIn("已批准", str(approved))
        self.assertIn("已拒绝", str(rejected))
        self.assertIn("仍待审批", str(waiting))
        self.assertNotIn("actions", str(approved))
        self.assertNotIn("actions", str(rejected))
        self.assertIn("actions", str(waiting))
        self.assertNotIn("部署完成", str(approved))

    def test_parses_only_exact_minimal_payload(self) -> None:
        self.assertEqual(
            parse_approval_card_action(
                {"action": "approve", "request_id": str(REQUEST_ID)}
            ),
            ApprovalCardAction("approve", REQUEST_ID),
        )
        for value in (
            None,
            {},
            {"action": "approve"},
            {"action": "unknown", "request_id": str(REQUEST_ID)},
            {"action": "approve", "request_id": "invalid"},
            {
                "action": "approve",
                "request_id": str(REQUEST_ID),
                "repository": "untrusted/value",
            },
        ):
            with self.subTest(value=value):
                self.assertIsNone(parse_approval_card_action(value))

    def test_callback_feedback_updates_only_authoritative_valid_card(
        self,
    ) -> None:
        approved = build_card_callback_response(
            ApprovalOutcome("recorded", "approved"),
            notification(),
        )
        waiting = build_card_callback_response(
            ApprovalOutcome("waiting"),
            notification(),
        )
        unauthorized = build_card_callback_response(
            ApprovalOutcome("unauthorized"),
            None,
        )
        self.assertEqual(approved["card"]["type"], "raw")
        self.assertIn("已批准", str(approved["card"]["data"]))
        self.assertIn("仍待审批", str(waiting["card"]["data"]))
        self.assertNotIn("card", unauthorized)


if __name__ == "__main__":
    unittest.main()
