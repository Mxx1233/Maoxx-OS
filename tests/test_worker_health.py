import json
import unittest
import urllib.error
import urllib.request

from app.services.worker_health import WorkerHealth, start_health_server


class WorkerHealthTests(unittest.TestCase):
    def test_state_transitions_and_counters(self) -> None:
        health = WorkerHealth()
        self.assertEqual(health.snapshot().status, "starting")
        self.assertFalse(health.snapshot().connected)

        health.mark_connected()
        health.mark_event_received()
        health.mark_event_succeeded()
        snapshot = health.snapshot()
        self.assertEqual(snapshot.status, "ready")
        self.assertTrue(snapshot.connected)
        self.assertEqual(snapshot.events_received, 1)
        self.assertEqual(snapshot.events_succeeded, 1)

        health.mark_reconnecting()
        health.mark_event_failed()
        snapshot = health.snapshot()
        self.assertEqual(snapshot.status, "reconnecting")
        self.assertFalse(snapshot.connected)
        self.assertEqual(snapshot.events_failed, 1)

    def test_http_health_requires_connection(self) -> None:
        health = WorkerHealth()
        server = start_health_server(health, "127.0.0.1", 0)
        url = f"http://127.0.0.1:{server.server_port}/health"
        try:
            with self.assertRaises(urllib.error.HTTPError) as context:
                urllib.request.urlopen(url, timeout=2)
            self.assertEqual(context.exception.code, 503)

            health.mark_connected()
            with urllib.request.urlopen(url, timeout=2) as response:
                payload = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertTrue(payload["connected"])
            self.assertNotIn("app_secret", payload)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
