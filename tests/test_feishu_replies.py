import unittest

from app.services.feishu_replies import recorded_reply


class FeishuReplyTests(unittest.TestCase):
    def test_recorded_reply_does_not_echo_sensitive_input(self) -> None:
        sensitive_input = "private health and career details"

        reply = recorded_reply()

        self.assertEqual(reply, "已记录。")
        self.assertNotIn(sensitive_input, reply)


if __name__ == "__main__":
    unittest.main()
