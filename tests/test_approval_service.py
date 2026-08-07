import unittest
from unittest.mock import Mock, patch
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.services.approval_service import (
    ApprovalCommand,
    ApprovalOutcome,
    ApprovalValidationError,
    approval_identity_fingerprint,
    approval_outcome_reply,
    parse_approval_command,
    process_approval_message,
    validate_approval_request_fields,
)


REQUEST_ID = UUID("aaaaaaaa-1111-4111-8111-111111111111")


class ApprovalServiceTests(unittest.TestCase):
    def test_parses_strict_approve_and_reject_commands(self) -> None:
        approved = parse_approval_command(f"批准 {REQUEST_ID}")
        rejected = parse_approval_command(f"拒绝 {REQUEST_ID}")
        self.assertEqual(
            approved.command,
            ApprovalCommand("approved", REQUEST_ID),
        )
        self.assertEqual(
            rejected.command,
            ApprovalCommand("rejected", REQUEST_ID),
        )

    def test_malformed_approval_like_command_fails_closed(self) -> None:
        for value in (
            "批准",
            "批准 not-a-uuid",
            f"批准 {str(REQUEST_ID).upper()}",
            f"批准 {REQUEST_ID} extra",
            "拒绝anything",
        ):
            with self.subTest(value=value):
                parsed = parse_approval_command(value)
                self.assertTrue(parsed.approval_like)
                self.assertIsNone(parsed.command)

    def test_normal_text_is_not_an_approval_command(self) -> None:
        self.assertFalse(
            parse_approval_command("记录今天的进展").approval_like
        )

    def test_full_identity_fingerprint_is_stable_and_non_reversible(
        self,
    ) -> None:
        fingerprint = approval_identity_fingerprint("fake-open-id")
        self.assertEqual(len(fingerprint), 64)
        self.assertEqual(
            fingerprint,
            approval_identity_fingerprint("fake-open-id"),
        )
        self.assertNotIn("fake-open-id", fingerprint)

    def test_validates_action_sha_environment_ttl_and_pr(self) -> None:
        validate_approval_request_fields(
            action_code="merge_pr",
            repository="Mxx1233/Maoxx-OS",
            pull_request_number=14,
            target_sha="a" * 40,
            target_environment="staging",
            idempotency_key="merge-14-a",
            ttl_seconds=1800,
        )
        invalid_cases = (
            {"target_sha": "A" * 40},
            {"ttl_seconds": 86401},
            {"repository": "https://example.invalid/repo"},
            {"target_environment": "production"},
            {"pull_request_number": None},
            {"idempotency_key": "unsafe key"},
        )
        base = {
            "action_code": "merge_pr",
            "repository": "Mxx1233/Maoxx-OS",
            "pull_request_number": 14,
            "target_sha": "a" * 40,
            "target_environment": "staging",
            "idempotency_key": "merge-14-a",
            "ttl_seconds": 1800,
        }
        for changed in invalid_cases:
            with self.subTest(changed=changed):
                with self.assertRaises(ApprovalValidationError):
                    validate_approval_request_fields(**(base | changed))

    @patch("app.services.approval_service.record_approval_decision")
    def test_dedicated_approver_and_exact_chat_are_required(
        self,
        record: Mock,
    ) -> None:
        record.return_value = ApprovalOutcome("recorded", "approved")
        common = {
            "db": Mock(),
            "text": f"批准 {REQUEST_ID}",
            "tenant_key": "tenant-test",
            "sender_type": "user",
            "sender_open_id": "approver-test",
            "chat_type": "p2p",
            "chat_id": "chat-test",
            "feishu_event_id": "event-test",
            "actor_user_id": REQUEST_ID,
            "allowed_tenant_keys": frozenset({"tenant-test"}),
            "allowed_open_ids": frozenset({"approver-test"}),
            "allowed_chat_types": frozenset({"p2p"}),
            "approver_open_ids": frozenset({"approver-test"}),
            "supervision_chat_id": "chat-test",
        }
        outcome = process_approval_message(**common)
        self.assertEqual(outcome, ApprovalOutcome("recorded", "approved"))
        record.assert_called_once()

        for changed in (
            {"tenant_key": "wrong"},
            {"sender_open_id": "wrong"},
            {"chat_type": "group"},
            {"approver_open_ids": frozenset()},
            {"chat_id": "wrong"},
            {"supervision_chat_id": ""},
        ):
            with self.subTest(changed=changed):
                self.assertEqual(
                    process_approval_message(**(common | changed)),
                    ApprovalOutcome("unauthorized"),
                )

    def test_outcome_replies_are_fixed_and_safe(self) -> None:
        for outcome in (
            ApprovalOutcome("malformed"),
            ApprovalOutcome("unauthorized"),
            ApprovalOutcome("expired"),
            ApprovalOutcome("recorded", "approved"),
            ApprovalOutcome("recorded", "rejected"),
        ):
            reply = approval_outcome_reply(outcome)
            self.assertNotIn(str(REQUEST_ID), reply)
            self.assertNotIn("fake-open-id", reply)

    def test_database_query_failure_fails_closed(self) -> None:
        db = Mock()
        db.execute.side_effect = SQLAlchemyError("private database detail")
        from app.services.approval_service import record_approval_decision

        outcome = record_approval_decision(
            db,
            command=ApprovalCommand("approved", REQUEST_ID),
            feishu_event_id="event-test",
            actor_user_id=REQUEST_ID,
            actor_open_id="fake-open-id",
        )
        self.assertEqual(outcome, ApprovalOutcome("unavailable"))
        db.rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
