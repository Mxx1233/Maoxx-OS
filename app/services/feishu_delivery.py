import logging
import random
import time
from dataclasses import dataclass
from typing import Callable, Protocol


logger = logging.getLogger("maoxx-feishu-worker")

TRANSIENT_RESPONSE_CODES = frozenset({99991400, 99991663})
TRANSIENT_EXCEPTIONS = (TimeoutError, ConnectionError, OSError)


class ReplyResponse(Protocol):
    code: int

    def success(self) -> bool: ...


@dataclass(frozen=True)
class DeliveryResult:
    sent: bool
    attempts: int
    failure_reason: str | None = None


def deliver_with_retry(
    send_once: Callable[[], ReplyResponse],
    *,
    max_attempts: int,
    backoff_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    random_value: Callable[[], float] = random.random,
) -> DeliveryResult:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if backoff_seconds < 0:
        raise ValueError("backoff_seconds cannot be negative")

    for attempt in range(1, max_attempts + 1):
        try:
            response = send_once()
        except TRANSIENT_EXCEPTIONS as exc:
            if attempt == max_attempts:
                logger.error(
                    "event=feishu_reply_exhausted reason=transport_error "
                    "exception_type=%s attempts=%s",
                    type(exc).__name__,
                    attempt,
                )
                return DeliveryResult(False, attempt, "transport_error")
            logger.warning(
                "event=feishu_reply_retry reason=transport_error "
                "exception_type=%s attempt=%s",
                type(exc).__name__,
                attempt,
            )
        else:
            if response.success():
                return DeliveryResult(True, attempt)

            code = int(response.code)
            if code not in TRANSIENT_RESPONSE_CODES:
                logger.error(
                    "event=feishu_reply_failed reason=permanent_error code=%s",
                    code,
                )
                return DeliveryResult(False, attempt, "permanent_error")
            if attempt == max_attempts:
                logger.error(
                    "event=feishu_reply_exhausted reason=rate_limited "
                    "code=%s attempts=%s",
                    code,
                    attempt,
                )
                return DeliveryResult(False, attempt, "rate_limited")
            logger.warning(
                "event=feishu_reply_retry reason=rate_limited code=%s attempt=%s",
                code,
                attempt,
            )

        delay = backoff_seconds * (2 ** (attempt - 1))
        sleep(delay + (delay * random_value()))

    raise AssertionError("retry loop must return")
