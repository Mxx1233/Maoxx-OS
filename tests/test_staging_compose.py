import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.staging.yml"


class StagingComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("docker") is None:
            raise unittest.SkipTest(
                "Docker CLI is required for Compose static tests"
            )
        env = os.environ.copy()
        env["STAGING_API_IMAGE"] = "maoxx-os-staging-api:" + "a" * 40
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(ROOT / ".env.staging.example"),
                "-p",
                "maoxx-staging",
                "-f",
                str(COMPOSE),
                "--profile",
                "feishu-test",
                "config",
                "--no-env-resolution",
                "--format",
                "json",
            ],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        cls.config = json.loads(result.stdout)
        services_result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(ROOT / ".env.staging.example"),
                "-p",
                "maoxx-staging",
                "-f",
                str(COMPOSE),
                "config",
                "--no-env-resolution",
                "--services",
            ],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        cls.default_services = set(services_result.stdout.splitlines())

    def test_default_services_and_worker_profile(self) -> None:
        services = self.config["services"]
        self.assertEqual(set(services), {"db", "api", "feishu-worker"})
        self.assertEqual(
            services["feishu-worker"]["profiles"], ["feishu-test"]
        )
        self.assertEqual(self.default_services, {"db", "api"})

    def test_isolation_and_resources(self) -> None:
        db = self.config["services"]["db"]
        api = self.config["services"]["api"]
        self.assertNotIn("ports", db)
        self.assertEqual(set(db["networks"]), {"staging_internal"})
        self.assertEqual(
            set(api["networks"]), {"staging_internal", "staging_api"}
        )
        self.assertTrue(
            self.config["networks"]["staging_internal"]["internal"]
        )
        self.assertEqual(
            api["ports"],
            [
                {
                    "mode": "ingress",
                    "target": 8000,
                    "published": "18000",
                    "protocol": "tcp",
                    "host_ip": "127.0.0.1",
                }
            ],
        )
        self.assertEqual(int(db["mem_limit"]), 268435456)
        self.assertEqual(int(api["mem_limit"]), 402653184)

    def test_forbidden_compose_features_absent(self) -> None:
        text = COMPOSE.read_text()
        for forbidden in (
            "container_name:",
            "external:",
            "network_mode:",
            "privileged:",
            "/var/run/docker.sock",
            "extra_hosts:",
        ):
            self.assertNotIn(forbidden, text)
        for service in self.config["services"].values():
            self.assertEqual(service["restart"], "no")

    def test_dockerignore_excludes_runtime_inputs(self) -> None:
        rules = set((ROOT / ".dockerignore").read_text().splitlines())
        for rule in (
            ".env",
            ".env.*",
            ".env.staging",
            "storage",
            ".git",
            "backups",
            "test-data",
            "tmp",
        ):
            self.assertIn(rule, rules)


if __name__ == "__main__":
    unittest.main()
