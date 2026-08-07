import unittest
from dataclasses import dataclass

from app.services.feishu_messaging import send_structured_text


@dataclass
class FakeResponse:
    ok: bool
    code: int = 0

    def success(self) -> bool:
        return self.ok


class FakeMessageApi:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def create(self, request):
        self.requests.append(request)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    def __init__(self, responses):
        message = FakeMessageApi(responses)
        self.message = message
        self.im = type(
            "Im",
            (),
            {"v1": type("V1", (), {"message": message})()},
        )()


class FeishuMessagingTests(unittest.TestCase):
    def test_proactive_send_success(self) -> None:
        client = FakeClient([FakeResponse(True)])
        result = send_structured_text(
            client,
            chat_id="fake-chat-id",
            text="fixed test message",
            max_attempts=3,
            backoff_seconds=0,
        )
        self.assertTrue(result.sent)
        self.assertEqual(len(client.message.requests), 1)

    def test_retries_rate_limit_and_exhaustion(self) -> None:
        retry_client = FakeClient(
            [FakeResponse(False, 99991400), FakeResponse(True)]
        )
        self.assertTrue(
            send_structured_text(
                retry_client,
                chat_id="fake-chat-id",
                text="fixed test message",
                max_attempts=2,
                backoff_seconds=0,
            ).sent
        )
        exhausted = FakeClient([TimeoutError("private") for _ in range(2)])
        result = send_structured_text(
            exhausted,
            chat_id="fake-chat-id",
            text="fixed test message",
            max_attempts=2,
            backoff_seconds=0,
        )
        self.assertFalse(result.sent)
        self.assertEqual(result.failure_reason, "transport_error")

        rate_limited = FakeClient(
            [FakeResponse(False, 99991400) for _ in range(2)]
        )
        result = send_structured_text(
            rate_limited,
            chat_id="fake-chat-id",
            text="fixed test message",
            max_attempts=2,
            backoff_seconds=0,
        )
        self.assertFalse(result.sent)
        self.assertEqual(result.failure_reason, "rate_limited")

    def test_permanent_error_is_not_retried(self) -> None:
        client = FakeClient([FakeResponse(False, 400)])
        result = send_structured_text(
            client,
            chat_id="fake-chat-id",
            text="fixed test message",
            max_attempts=3,
            backoff_seconds=0,
        )
        self.assertFalse(result.sent)
        self.assertEqual(result.attempts, 1)

    def test_missing_chat_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            send_structured_text(
                FakeClient([FakeResponse(True)]),
                chat_id="",
                text="fixed test message",
                max_attempts=1,
                backoff_seconds=0,
            )


if __name__ == "__main__":
    unittest.main()
