import unittest

from pydantic import ValidationError

from app.config import Settings, parse_csv_set


class ParseCsvSetTests(unittest.TestCase):
    def test_parses_trims_and_deduplicates_values(self) -> None:
        self.assertEqual(
            parse_csv_set("first, second,first,, "),
            frozenset({"first", "second"}),
        )

    def test_empty_value_is_fail_closed_empty_set(self) -> None:
        self.assertEqual(parse_csv_set(""), frozenset())

    def test_approval_configuration_defaults_fail_closed(self) -> None:
        configured = Settings(
            database_url="postgresql+psycopg://example.invalid/test",
            default_user_id="00000000-0000-0000-0000-000000000001",
            feishu_app_id="fake-app-id",
            feishu_app_secret="fake-app-secret",
        )
        self.assertFalse(configured.approval_configuration_valid)
        self.assertEqual(configured.feishu_approval_ttl_seconds, 1800)

    def test_approval_configuration_requires_chat_and_approver(self) -> None:
        configured = Settings(
            database_url="postgresql+psycopg://example.invalid/test",
            default_user_id="00000000-0000-0000-0000-000000000001",
            feishu_app_id="fake-app-id",
            feishu_app_secret="fake-app-secret",
            feishu_supervision_chat_id="fake-chat-id",
            feishu_approver_open_ids="fake-approver-id",
        )
        self.assertTrue(configured.approval_configuration_valid)
        self.assertEqual(
            configured.approver_open_ids,
            frozenset({"fake-approver-id"}),
        )

    def test_approval_ttl_above_maximum_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                database_url="postgresql+psycopg://example.invalid/test",
                default_user_id="00000000-0000-0000-0000-000000000001",
                feishu_app_id="fake-app-id",
                feishu_app_secret="fake-app-secret",
                feishu_approval_ttl_seconds=86401,
            )


if __name__ == "__main__":
    unittest.main()
