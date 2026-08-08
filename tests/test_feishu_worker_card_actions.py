import json
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

from app.services.approval_service import ApprovalOutcome


REQUEST_ID = UUID("11111111-1111-4111-8111-111111111111")


def fake_callback(action: str = "approve") -> str:
    return json.dumps(
        {
            "schema": "2.0",
            "header": {
                "event_id": "callback-event-test",
                "event_type": "card.action.trigger",
                "tenant_key": "tenant-test",
            },
            "event": {
                "operator": {
                    "tenant_key": "tenant-test",
                    "open_id": "approver-test",
                },
                "context": {
                    "open_chat_id": "chat-test",
                    "open_message_id": "message-test",
                },
                "action": {
                    "tag": "button",
                    "value": {
                        "action": action,
                        "request_id": str(REQUEST_ID),
                    },
                },
            },
        }
    )


class FeishuWorkerCardActionTests(unittest.TestCase):
    def setUp(self) -> None:
        from app import feishu_worker

        self.worker = feishu_worker

    def _request(self):
        return SimpleNamespace(
            id=REQUEST_ID,
            repository="Authoritative/Repository",
            target_sha="a" * 40,
            pull_request_number=14,
            target_environment="production",
            action_code="production_deploy",
            expires_at=datetime(2026, 8, 8, 12, tzinfo=UTC),
        )

    def test_approve_reject_and_wait_use_dedicated_callback_path(self) -> None:
        cases = (
            ("approve", ApprovalOutcome("recorded", "approved"), "已批准"),
            ("reject", ApprovalOutcome("recorded", "rejected"), "已拒绝"),
            ("wait", ApprovalOutcome("waiting"), "仍待审批"),
        )
        for action, outcome, expected in cases:
            with self.subTest(action=action):
                db = MagicMock()
                db.get.return_value = self._request()
                session = MagicMock()
                session.__enter__.return_value = db
                with (
                    patch.object(
                        self.worker.lark.JSON,
                        "marshal",
                        return_value=fake_callback(action),
                    ),
                    patch.object(
                        self.worker,
                        "SessionLocal",
                        return_value=session,
                    ),
                    patch.object(
                        self.worker,
                        "process_approval_card_action",
                        return_value=outcome,
                    ) as process,
                ):
                    response = self.worker.handle_card_action(MagicMock())
                process.assert_called_once()
                self.assertEqual(process.call_args.kwargs["action"], action)
                response_json = json.loads(
                    self.worker.lark.JSON.marshal(response)
                )
                self.assertIn(
                    expected, json.dumps(response_json, ensure_ascii=False)
                )

    def test_card_response_reloads_authoritative_request_from_database(
        self,
    ) -> None:
        db = MagicMock()
        db.get.return_value = self._request()
        session = MagicMock()
        session.__enter__.return_value = db
        with (
            patch.object(
                self.worker.lark.JSON,
                "marshal",
                return_value=fake_callback(),
            ),
            patch.object(
                self.worker,
                "SessionLocal",
                return_value=session,
            ),
            patch.object(
                self.worker,
                "process_approval_card_action",
                return_value=ApprovalOutcome("recorded", "approved"),
            ),
        ):
            response = self.worker.handle_card_action(MagicMock())
        db.get.assert_called_once_with(self.worker.ApprovalRequest, REQUEST_ID)
        response_json = json.loads(self.worker.lark.JSON.marshal(response))
        serialized = json.dumps(response_json, ensure_ascii=False)
        self.assertIn("Authoritative/Repository", serialized)

    def test_missing_or_malformed_callback_fields_fail_closed(self) -> None:
        malformed_payloads = (
            json.dumps({"header": {}, "event": {}}),
            json.dumps(
                {
                    "header": {"event_id": "event", "tenant_key": "tenant"},
                    "event": {
                        "action": {
                            "value": {
                                "action": "approve",
                                "request_id": "not-a-uuid",
                            }
                        }
                    },
                }
            ),
        )
        for payload in malformed_payloads:
            with self.subTest(payload=payload):
                with (
                    patch.object(
                        self.worker.lark.JSON,
                        "marshal",
                        return_value=payload,
                    ),
                    patch.object(
                        self.worker,
                        "process_approval_card_action",
                    ) as process,
                ):
                    response = self.worker.handle_card_action(MagicMock())
                process.assert_not_called()
                response_json = json.loads(
                    self.worker.lark.JSON.marshal(response)
                )
                self.assertEqual(response_json["toast"]["type"], "error")

    def test_websocket_dispatcher_registers_card_callback(self) -> None:
        self.assertIn(
            "p2.card.action.trigger",
            self.worker.event_handler._callback_processor_map,
        )


if __name__ == "__main__":
    unittest.main()
