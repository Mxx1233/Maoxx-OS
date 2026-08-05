import unittest
from dataclasses import dataclass

from app.services.feishu_delivery import deliver_with_retry


@dataclass
class FakeResponse:
    ok: bool
    code: int = 0

    def success(self) -> bool:
        return self.ok


class FeishuDeliveryTests(unittest.TestCase):
    def test_returns_after_success(self) -> None:
        result = deliver_with_retry(
            lambda: FakeResponse(True),
            max_attempts=3,
            backoff_seconds=1,
            sleep=lambda _: None,
        )
        self.assertTrue(result.sent)
        self.assertEqual(result.attempts, 1)

    def test_retries_rate_limit_with_exponential_backoff_and_jitter(self) -> None:
        responses = iter(
            [FakeResponse(False, 99991400), FakeResponse(True)]
        )
        delays: list[float] = []
        result = deliver_with_retry(
            lambda: next(responses),
            max_attempts=3,
            backoff_seconds=2,
            sleep=delays.append,
            random_value=lambda: 0.25,
        )
        self.assertTrue(result.sent)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(delays, [2.5])

    def test_does_not_retry_permanent_error(self) -> None:
        delays: list[float] = []
        result = deliver_with_retry(
            lambda: FakeResponse(False, 400),
            max_attempts=3,
            backoff_seconds=1,
            sleep=delays.append,
        )
        self.assertFalse(result.sent)
        self.assertEqual(result.failure_reason, "permanent_error")
        self.assertEqual(delays, [])

    def test_retries_transport_error_until_exhausted(self) -> None:
        delays: list[float] = []

        def fail() -> FakeResponse:
            raise TimeoutError("private response content")

        result = deliver_with_retry(
            fail,
            max_attempts=3,
            backoff_seconds=1,
            sleep=delays.append,
            random_value=lambda: 0,
        )
        self.assertFalse(result.sent)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(result.failure_reason, "transport_error")
        self.assertEqual(delays, [1, 2])

    def test_transport_error_log_does_not_expose_exception_content(self) -> None:
        sensitive_content = "private-message-body"

        def fail() -> FakeResponse:
            raise TimeoutError(sensitive_content)

        with self.assertLogs("maoxx-feishu-worker", level="ERROR") as logs:
            deliver_with_retry(
                fail,
                max_attempts=1,
                backoff_seconds=0,
            )
        self.assertNotIn(sensitive_content, "\n".join(logs.output))

    def test_rejects_invalid_retry_configuration(self) -> None:
        with self.assertRaises(ValueError):
            deliver_with_retry(
                lambda: FakeResponse(True),
                max_attempts=0,
                backoff_seconds=0,
            )


if __name__ == "__main__":
    unittest.main()
