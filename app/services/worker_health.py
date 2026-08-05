import json
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


WORKER_HEALTH_HOST = "127.0.0.1"
WORKER_HEALTH_PORT = 8081


def utc_now_text() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class WorkerSnapshot:
    status: str
    connected: bool
    started_at: str
    connected_at: str | None
    last_event_at: str | None
    last_success_at: str | None
    last_failure_at: str | None
    events_received: int
    events_succeeded: int
    events_failed: int


class WorkerHealth:
    """Thread-safe, in-memory operational state for one Worker process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = "starting"
        self._connected = False
        self._started_at = utc_now_text()
        self._connected_at: str | None = None
        self._last_event_at: str | None = None
        self._last_success_at: str | None = None
        self._last_failure_at: str | None = None
        self._events_received = 0
        self._events_succeeded = 0
        self._events_failed = 0

    def mark_connected(self) -> None:
        with self._lock:
            self._status = "ready"
            self._connected = True
            self._connected_at = utc_now_text()

    def mark_reconnecting(self) -> None:
        with self._lock:
            self._status = "reconnecting"
            self._connected = False

    def mark_event_received(self) -> None:
        with self._lock:
            self._last_event_at = utc_now_text()
            self._events_received += 1

    def mark_event_succeeded(self) -> None:
        with self._lock:
            self._last_success_at = utc_now_text()
            self._events_succeeded += 1

    def mark_event_failed(self) -> None:
        with self._lock:
            self._last_failure_at = utc_now_text()
            self._events_failed += 1

    def snapshot(self) -> WorkerSnapshot:
        with self._lock:
            return WorkerSnapshot(
                status=self._status,
                connected=self._connected,
                started_at=self._started_at,
                connected_at=self._connected_at,
                last_event_at=self._last_event_at,
                last_success_at=self._last_success_at,
                last_failure_at=self._last_failure_at,
                events_received=self._events_received,
                events_succeeded=self._events_succeeded,
                events_failed=self._events_failed,
            )


def start_health_server(
    health: WorkerHealth,
    host: str,
    port: int,
) -> ThreadingHTTPServer:
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - standard library API
            if self.path != "/health":
                self.send_error(404)
                return

            snapshot = health.snapshot()
            body = json.dumps(asdict(snapshot)).encode("utf-8")
            self.send_response(200 if snapshot.connected else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), HealthHandler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="worker-health-server",
        daemon=True,
    )
    thread.start()
    return server
