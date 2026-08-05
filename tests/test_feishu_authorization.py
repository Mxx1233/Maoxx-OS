import unittest

from app.services.feishu_authorization import (
    authorize_feishu_message,
    identifier_fingerprint,
)


ALLOWED_TENANTS = frozenset({"tenant-allowed"})
ALLOWED_SENDERS = frozenset({"sender-allowed"})
ALLOWED_CHAT_TYPES = frozenset({"p2p"})


def authorize(**overrides: object):
    values = {
        "tenant_key": "tenant-allowed",
        "sender_type": "user",
        "sender_open_id": "sender-allowed",
        "chat_type": "p2p",
        "allowed_tenant_keys": ALLOWED_TENANTS,
        "allowed_open_ids": ALLOWED_SENDERS,
        "allowed_chat_types": ALLOWED_CHAT_TYPES,
    }
    values.update(overrides)
    return authorize_feishu_message(**values)  # type: ignore[arg-type]


class FeishuAuthorizationTests(unittest.TestCase):
    def test_allows_fully_authorized_p2p_user(self) -> None:
        self.assertTrue(authorize().allowed)

    def test_rejects_tenant_before_other_checks(self) -> None:
        decision = authorize(
            tenant_key="tenant-denied",
            sender_type="bot",
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "tenant_not_allowed")

    def test_rejects_non_user_sender(self) -> None:
        decision = authorize(sender_type="bot")
        self.assertEqual(decision.reason, "sender_type_not_allowed")

    def test_rejects_unlisted_sender(self) -> None:
        decision = authorize(sender_open_id="sender-denied")
        self.assertEqual(decision.reason, "sender_not_allowed")

    def test_rejects_group_chat(self) -> None:
        decision = authorize(chat_type="group")
        self.assertEqual(decision.reason, "chat_type_not_allowed")

    def test_empty_allowlists_reject(self) -> None:
        decision = authorize(allowed_tenant_keys=frozenset())
        self.assertEqual(decision.reason, "tenant_not_allowed")

    def test_log_fingerprint_does_not_expose_identifier(self) -> None:
        identifier = "sensitive-identifier"
        fingerprint = identifier_fingerprint(identifier)
        self.assertNotIn(identifier, fingerprint)
        self.assertEqual(len(fingerprint), 12)


if __name__ == "__main__":
    unittest.main()
