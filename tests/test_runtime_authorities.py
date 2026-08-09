import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.services.deployment_authority import GitHubCliProtectedMainCiVerifier
from app.services.deployment_policy import required_health_checks
from app.services.production_supervisor import (
    DockerComposeSupervisorRuntime,
    SupervisorAttempt,
    SupervisorError,
    calculate_intent_hash,
)
from app.services.resource_authority import (
    MEM_MEDIAN_MIN,
    MEM_SAMPLE_MIN,
    SWAP_SAMPLE_MIN,
    ResourceAuthorityError,
    TrustedProductionResourceCollector,
    TrustedResourceSample,
)


DIGEST = "sha256:" + "a" * 64
SHA = "b" * 40


class CommandFixture:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(argv)
        key = argv[-1] if argv[:2] == ["gh", "api"] else tuple(argv)
        response = self.responses.get(key)
        if response is None:
            return SimpleNamespace(returncode=1, stdout="", stderr="missing")
        if isinstance(response, tuple):
            code, payload = response
        else:
            code, payload = 0, response
        return SimpleNamespace(
            returncode=code,
            stdout=payload if isinstance(payload, str) else json.dumps(payload),
            stderr="",
        )


def github_fixture(*, attempt=2, quality_run=True):
    repository = "Mxx1233/Maoxx-OS"
    run_id = 123
    return {
        f"repos/{repository}": {
            "id": 456,
            "full_name": repository,
            "default_branch": "main",
        },
        f"repos/{repository}/actions/workflows/789": {
            "id": 789,
            "path": ".github/workflows/ci.yml",
            "state": "active",
        },
        f"repos/{repository}/actions/runs/{run_id}": {
            "id": run_id,
            "run_attempt": attempt,
            "workflow_id": 789,
            "head_sha": SHA,
            "head_branch": "main",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "actor": {"type": "User"},
            "repository": {"id": 456, "full_name": repository},
        },
        f"repos/{repository}/compare/{SHA}...main": {
            "merge_base_commit": {"sha": SHA}
        },
        f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100": {
            "jobs": [
                {
                    "id": 999,
                    "name": "CI / Quality Gate"
                    if quality_run
                    else "Other / Quality Gate",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        },
    }


def supervisor_attempt() -> SupervisorAttempt:
    deployment_id = uuid4()
    execution_id = uuid4()
    images = {"api": f"registry.example/maoxx@{DIGEST}"}
    intent_hash = calculate_intent_hash(
        deployment_id=deployment_id,
        execution_attempt_id=execution_id,
        fencing_epoch=3,
        repository="Mxx1233/Maoxx-OS",
        target_sha=SHA,
        service_images=images,
        artifact_digest=DIGEST,
        expected_migration_revision=None,
    )
    return SupervisorAttempt(
        deployment_id,
        execution_id,
        3,
        "Mxx1233/Maoxx-OS",
        SHA,
        intent_hash,
        images,
        DIGEST,
        None,
        datetime.now(UTC),
    )


class GitHubAuthorityTests(unittest.TestCase):
    def verifier(self, responses):
        return GitHubCliProtectedMainCiVerifier(
            repository="Mxx1233/Maoxx-OS",
            repository_id=456,
            workflow_id=789,
            workflow_path=".github/workflows/ci.yml",
            run=CommandFixture(responses),
        )

    def test_exact_same_run_attempt_quality_gate_is_authoritative(self):
        evidence = self.verifier(github_fixture()).verify(
            repository="Mxx1233/Maoxx-OS", target_sha=SHA, run_id=123
        )
        self.assertEqual(evidence.run_id, 123)
        self.assertEqual(evidence.provenance["run_attempt"], "2")
        self.assertEqual(evidence.provenance["quality_gate_job_id"], "999")

    def test_wrong_workflow_cross_run_auth_and_malformed_fail_closed(self):
        cases = []
        wrong_workflow = github_fixture()
        wrong_workflow["repos/Mxx1233/Maoxx-OS/actions/runs/123"][
            "workflow_id"
        ] = 999
        cases.append(wrong_workflow)
        cases.append(github_fixture(quality_run=False))
        auth = github_fixture()
        auth["repos/Mxx1233/Maoxx-OS"] = (1, "")
        cases.append(auth)
        malformed = github_fixture()
        malformed["repos/Mxx1233/Maoxx-OS"] = "not-json"
        cases.append(malformed)
        for fixture in cases:
            with self.subTest(fixture=fixture):
                with self.assertRaises(ValueError):
                    self.verifier(fixture).verify(
                        repository="Mxx1233/Maoxx-OS",
                        target_sha=SHA,
                        run_id=123,
                    )

    def test_wrong_repository_sha_event_and_ambiguous_quality_fail_closed(self):
        with self.assertRaises(ValueError):
            self.verifier(github_fixture()).verify(
                repository="Other/Repo", target_sha=SHA, run_id=123
            )
        for key, value in (("head_sha", "c" * 40), ("event", "pull_request")):
            fixture = github_fixture()
            fixture["repos/Mxx1233/Maoxx-OS/actions/runs/123"][key] = value
            with self.assertRaises(ValueError):
                self.verifier(fixture).verify(
                    repository="Mxx1233/Maoxx-OS",
                    target_sha=SHA,
                    run_id=123,
                )
        fixture = github_fixture()
        jobs_key = "repos/Mxx1233/Maoxx-OS/actions/runs/123/attempts/2/jobs?per_page=100"
        fixture[jobs_key]["jobs"] *= 2
        with self.assertRaises(ValueError):
            self.verifier(fixture).verify(
                repository="Mxx1233/Maoxx-OS", target_sha=SHA, run_id=123
            )


class ResourceAuthorityTests(unittest.TestCase):
    def samples(self, *, spacing=5.0, count=7, **overrides):
        check_id, deployment_id, nonce = uuid4(), uuid4(), uuid4()
        now = datetime.now(UTC)
        values = {
            "mem_available_bytes": 1_200_000_000,
            "swap_free_bytes": 1_500_000_000,
            "root_free_bytes": 6_000_000_000,
            "docker_free_bytes": 6_000_000_000,
        } | overrides
        return tuple(
            TrustedResourceSample(
                check_id,
                deployment_id,
                "production",
                "host:pre-mutation",
                nonce,
                now + timedelta(seconds=index * spacing),
                index * spacing,
                **values,
            )
            for index in range(count)
        )

    def test_exact_window_thresholds_and_freshness(self):
        collector = TrustedProductionResourceCollector()
        result = collector._evaluate(self.samples())
        self.assertTrue(result.passed)
        result.require_fresh(result.evaluated_at)
        self.assertEqual(result.median_mem_available_bytes, 1_200_000_000)

    def test_wrong_count_cadence_binding_and_thresholds_fail_closed(self):
        collector = TrustedProductionResourceCollector()
        for samples in (self.samples(count=6), self.samples(spacing=3.0)):
            with self.assertRaises(ResourceAuthorityError):
                collector._evaluate(samples)
        cases = (
            {"mem_available_bytes": MEM_MEDIAN_MIN - 1},
            {"mem_available_bytes": MEM_SAMPLE_MIN - 1},
            {"swap_free_bytes": SWAP_SAMPLE_MIN - 1},
            {"root_free_bytes": 5 * 1024**3 - 1},
            {"docker_free_bytes": 5 * 1024**3 - 1},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assertFalse(
                    collector._evaluate(self.samples(**overrides)).passed
                )

    def test_stale_samples_or_evaluator_cannot_be_repackaged(self):
        collector = TrustedProductionResourceCollector()
        result = collector._evaluate(self.samples())
        with self.assertRaises(ResourceAuthorityError):
            result.require_fresh(result.evaluated_at + timedelta(seconds=31))
        self.assertNotIn("passed", TrustedProductionResourceCollector.collect.__annotations__)


class ImmutableRuntimeTests(unittest.TestCase):
    def test_resolved_build_mutable_and_wrong_digest_are_rejected(self):
        item = supervisor_attempt()
        with tempfile.TemporaryDirectory() as directory:
            compose = Path(directory) / "compose.yml"
            compose.write_text("services: {}\n", encoding="utf-8")

            def run(argv, **_kwargs):
                if "config" in argv:
                    plan = {
                        "services": {
                            "api": {
                                "image": item.service_images["api"],
                                "build": {"context": "."},
                            }
                        }
                    }
                    return SimpleNamespace(
                        returncode=0, stdout=json.dumps(plan), stderr=""
                    )
                raise AssertionError(argv)

            runtime = DockerComposeSupervisorRuntime(
                compose_file=compose, run=run
            )
            with self.assertRaises(SupervisorError) as build:
                runtime.validate_plan(item)
            self.assertEqual(build.exception.code, "resolved_plan_not_immutable")

            for image in (
                "registry.example/maoxx:latest",
                "registry.example/maoxx@sha256:" + "c" * 64,
            ):
                forged = SupervisorAttempt(
                    *item.__dict__.values()
                )
                object.__setattr__(forged, "service_images", {"api": image})
                with self.assertRaises(SupervisorError):
                    runtime.validate_plan(forged)

    def test_health_contract_requires_every_selected_service_field(self):
        required = required_health_checks(("api", "feishu-worker"))
        expected = {
            "api.alive",
            "api.ready",
            "api.live",
            "api.http",
            "api.dependency.db",
            "feishu-worker.alive",
            "feishu-worker.ready",
            "feishu-worker.live",
            "feishu-worker.connected",
            "migration",
            "smoke",
            "critical_regression",
            "verification_within_timeout",
            "artifact_digest",
            "revision_sha",
        }
        self.assertEqual(required, expected)


def worker_attempt() -> SupervisorAttempt:
    """SupervisorAttempt whose service set includes feishu-worker."""
    item = supervisor_attempt()
    images = dict(item.service_images)
    images["feishu-worker"] = f"registry.example/maoxx-worker@{DIGEST}"
    intent_hash = calculate_intent_hash(
        deployment_id=item.deployment_id,
        execution_attempt_id=item.execution_attempt_id,
        fencing_epoch=item.fencing_epoch,
        repository=item.repository,
        target_sha=item.target_sha,
        service_images=images,
        artifact_digest=DIGEST,
        expected_migration_revision=item.expected_migration_revision,
    )
    forged = SupervisorAttempt(
        *item.__dict__.values()
    )
    object.__setattr__(forged, "service_images", images)
    object.__setattr__(forged, "intent_hash", intent_hash)
    return forged


def healthy_container(item: SupervisorAttempt, service: str, container_id: str) -> dict:
    labels = {
        "com.maoxx.deployment_id": str(item.deployment_id),
        "com.maoxx.execution_attempt_id": str(item.execution_attempt_id),
        "com.maoxx.fencing_epoch": str(item.fencing_epoch),
        "com.maoxx.intent_hash": item.intent_hash,
        "com.maoxx.target_sha": item.target_sha,
        "com.maoxx.image_digest": item.artifact_digest,
    }
    return {
        "Id": container_id,
        "Image": f"sha256:{service}_image_id",
        "RestartCount": 0,
        "State": {
            "Status": "running",
            "Running": True,
            "OOMKilled": False,
            "Health": {"Status": "healthy"},
        },
        "Config": {
            "Image": item.service_images[service],
            "Labels": labels,
        },
    }


class WorkerHealthRuntimeTests(unittest.TestCase):
    """H-1 regression: worker readiness is probed inside the container via
    docker compose exec on port 8081 (never a host port), and M-3: the health
    cycle polls until the deadline instead of deciding on a single probe."""

    def _runtime(
        self,
        item: SupervisorAttempt,
        worker_responses,
        *,
        sleep=None,
    ):
        compose = Path(tempfile.mkdtemp()) / "compose.yml"
        compose.write_text("services: {}\n", encoding="utf-8")
        calls: list = []

        def run(argv, **_kwargs):
            calls.append(argv)
            if argv[:4] == ["docker", "compose", "-f", str(compose)] and argv[4:6] == ["ps", "--format"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        [
                            {"Service": "api", "ID": "c1", "State": "running"},
                            {"Service": "feishu-worker", "ID": "c2", "State": "running"},
                        ]
                    ),
                    stderr="",
                )
            if argv[:2] == ["docker", "inspect"]:
                service = "api" if argv[2] == "c1" else "feishu-worker"
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps([healthy_container(item, service, argv[2])]),
                    stderr="",
                )
            if argv[:3] == ["docker", "image", "inspect"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        [
                            {
                                "Id": f"sha256:{'api' if 'worker' not in argv[3] else 'feishu-worker'}_image_id",
                                "RepoDigests": [argv[3]],
                            }
                        ]
                    ),
                    stderr="",
                )
            if argv[:6] == ["docker", "compose", "-f", str(compose), "exec", "-T"] and argv[6] == "api":
                return SimpleNamespace(
                    returncode=0, stdout="0003_phase_1d_f_deployment", stderr=""
                )
            if argv[:6] == ["docker", "compose", "-f", str(compose), "exec", "-T"] and argv[6] == "feishu-worker":
                return worker_responses.pop(0) if worker_responses else SimpleNamespace(
                    returncode=1, stdout="", stderr="worker probe failed"
                )
            if argv[0] == "curl":
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")
            if argv[0] == "pgrep":
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            raise AssertionError(f"unexpected argv: {argv}")

        runtime = DockerComposeSupervisorRuntime(
            compose_file=compose, run=run, sleep=sleep or (lambda _seconds: None)
        )
        return runtime, calls

    def test_worker_probe_uses_compose_exec_on_port_8081(self):
        item = worker_attempt()
        runtime, calls = self._runtime(
            item,
            [SimpleNamespace(returncode=1, stdout="", stderr="probe failed")],
        )
        deadline = datetime.now(UTC) + timedelta(seconds=3)
        with self.assertRaises(SupervisorError) as failed:
            runtime.verify_health(item, deadline=deadline)
        self.assertEqual(failed.exception.code, "mandatory_health_check_failed")
        # The worker probe must run inside the container on 8081 — never a
        # bare host curl to a non-published port.
        self.assertTrue(
            any(
                "8081" in " ".join(argv)
                and argv[6] == "feishu-worker"
                for argv in calls
                if "feishu-worker" in argv
            )
        )
        self.assertFalse(
            any("127.0.0.1:8787" in " ".join(argv) for argv in calls)
        )

    def test_health_cycle_polls_until_deadline(self):
        item = worker_attempt()
        runtime, calls = self._runtime(
            item,
            [
                SimpleNamespace(returncode=1, stdout="", stderr="not ready yet"),
                SimpleNamespace(
                    returncode=0, stdout='{"connected": true}', stderr=""
                ),
            ],
        )
        deadline = datetime.now(UTC) + timedelta(seconds=3)
        cycle = runtime.verify_health(item, deadline=deadline)
        self.assertTrue(cycle.checks["feishu-worker.connected"])
        self.assertTrue(cycle.checks["smoke"])


if __name__ == "__main__":
    unittest.main()
