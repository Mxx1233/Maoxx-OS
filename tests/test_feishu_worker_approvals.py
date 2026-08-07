import json
import unittest
from unittest.mock import MagicMock, patch

from app.services.approval_service import ApprovalOutcome


def fake_event(text: str) -> str:
    return json.dumps(
        {
            "header": {
                "tenant_key": "tenant-test",
                "event_id": "event-test",
            },
            "event": {
                "sender": {
                    "sender_type": "user",
                    "sender_id": {"open_id": "sender-test"},
                },
                "message": {
                    "message_id": "message-test",
                    "message_type": "text",
                    "chat_type": "p2p",
                    "chat_id": "chat-test",
                    "content": json.dumps({"text": text}),
                },
            },
        }
    )


class FeishuWorkerApprovalRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        from app import feishu_worker

        self.worker = feishu_worker

    def test_approval_command_returns_before_raw_input_persistence(
        self,
    ) -> None:
        with (
            patch.object(
                self.worker.lark.JSON,
                "marshal",
                return_value=fake_event(
                    "批准 11111111-1111-4111-8111-111111111111"
                ),
            ),
            patch.object(
                self.worker,
                "authorize_feishu_message",
                return_value=MagicMock(allowed=True),
            ),
            patch.object(
                self.worker,
                "process_approval_message",
                return_value=ApprovalOutcome("recorded", "approved"),
            ),
            patch.object(self.worker, "reply_text") as reply,
            patch.object(self.worker, "RawInput") as raw_input,
            patch.object(self.worker, "SessionLocal"),
        ):
            self.worker.handle_message(MagicMock())

        raw_input.assert_not_called()
        reply.assert_called_once_with(
            "message-test",
            "审批决定已记录：批准。",
        )

    def test_malformed_approval_command_is_not_persisted(self) -> None:
        with (
            patch.object(
                self.worker.lark.JSON,
                "marshal",
                return_value=fake_event("批准 invalid"),
            ),
            patch.object(
                self.worker,
                "authorize_feishu_message",
                return_value=MagicMock(allowed=True),
            ),
            patch.object(
                self.worker,
                "process_approval_message",
                return_value=ApprovalOutcome("malformed"),
            ),
            patch.object(self.worker, "reply_text"),
            patch.object(self.worker, "RawInput") as raw_input,
            patch.object(self.worker, "SessionLocal"),
        ):
            self.worker.handle_message(MagicMock())

        raw_input.assert_not_called()

    def test_normal_text_keeps_existing_raw_input_path(self) -> None:
        record = MagicMock()
        record.id = "record-test"
        db = MagicMock()
        session_local = MagicMock()
        session_local.return_value.__enter__.return_value = db
        with (
            patch.object(
                self.worker.lark.JSON,
                "marshal",
                return_value=fake_event("记录今天的进展"),
            ),
            patch.object(
                self.worker,
                "authorize_feishu_message",
                return_value=MagicMock(allowed=True),
            ),
            patch.object(
                self.worker,
                "process_approval_message",
                return_value=None,
            ),
            patch.object(self.worker, "reply_text") as reply,
            patch.object(
                self.worker,
                "RawInput",
                return_value=record,
            ) as raw_input,
            patch.object(self.worker, "SessionLocal", session_local),
        ):
            self.worker.handle_message(MagicMock())

        raw_input.assert_called_once()
        self.assertEqual(db.add.call_count, 1)
        db.commit.assert_called_once()
        reply.assert_called_once_with("message-test", "已记录。")


if __name__ == "__main__":
    unittest.main()
