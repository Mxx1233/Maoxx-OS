import json
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.services.deployment_authority import RuntimeState
from app.services.deployment_policy import required_health_checks
from app.services.production_supervisor import (
    DurableExecutionJournal,
    HealthCycle,
    JournalEvent,
    ProductionDeploymentSupervisor,
    RuntimeSnapshot,
    SupervisorAttempt,
    SupervisorError,
    calculate_intent_hash,
)
from app.services.resource_authority import (
    TrustedResourceResult,
    TrustedResourceSample,
)


DIGEST = "sha256:" + "a" * 64
SHA = "b" * 40


def attempt() -> SupervisorAttempt:
    deployment_id = uuid4()
    execution_id = uuid4()
    images = {"api": f"registry.example/maoxx@{DIGEST}"}
    intent_hash = calculate_intent_hash(
        deployment_id=deployment_id,
        execution_attempt_id=execution_id,
        fencing_epoch=7,
        repository="Mxx1233/Maoxx-OS",
        target_sha=SHA,
        service_images=images,
        artifact_digest=DIGEST,
        expected_migration_revision="0003_phase_1d_f_deployment",
    )
    return SupervisorAttempt(
        deployment_id,
        execution_id,
        7,
        "Mxx1233/Maoxx-OS",
        SHA,
        intent_hash,
        images,
        DIGEST,
        "0003_phase_1d_f_deployment",
        datetime.now(UTC),
    )


def resource_result(item: SupervisorAttempt) -> TrustedResourceResult:
    check_id = uuid4()
    nonce = uuid4()
    now = datetime.now(UTC)
    samples = tuple(
        TrustedResourceSample(
            check_id,
            item.deployment_id,
            "production",
            "host:pre-mutation",
            nonce,
            now,
            index * 5.0,
            1_200_000_000,
            1_500_000_000,
            6_000_000_000,
            6_000_000_000,
        )
        for index in range(7)
    )
    return TrustedResourceResult(
        check_id,
        item.deployment_id,
        nonce,
        samples,
        now,
        True,
        1_200_000_000,
        1_200_000_000,
        1_500_000_000,
        6_000_000_000,
        6_000_000_000,
    )


class Authority:
    def __init__(self, item: SupervisorAttempt) -> None:
        self.item = item
        self.resources = []
        self.results = []
        self.mutation_markers: list = []

    def load(self, execution_id):
        if execution_id != self.item.execution_attempt_id:
            raise SupervisorError("unknown_execution_attempt")
        return self.item

    def record_resource(self, result):
        self.resources.append(result)

    def record_runtime_result(self, item, state, *, source):
        self.results.append((item.execution_attempt_id, state, source))

    def record_mutation_submitted(self, item):
        self.mutation_markers.append(item.execution_attempt_id)

    def mutation_submitted(self, item):
        return item.execution_attempt_id in self.mutation_markers


class Collector:
    def __init__(self, result) -> None:
        self.result = result

    def collect(self, deployment_id):
        if deployment_id != self.result.deployment_id:
            raise AssertionError("wrong binding")
        return self.result


class Runtime:
    def __init__(self, item: SupervisorAttempt) -> None:
        self.item = item
        self.submissions = 0
        self.state = RuntimeState.NOT_STARTED
        self.client_active = False
        self.transitional = False
        self.events = False
        self.submit_started = threading.Event()
        self.submit_release = threading.Event()
        self.block_submit = False

    def validate_plan(self, item):
        self.assert_attempt(item)

    def submit(self, item):
        self.assert_attempt(item)
        self.submissions += 1
        self.submit_started.set()
        if self.block_submit:
            self.submit_release.wait(timeout=3)
        self.state = RuntimeState.MUTATION_COMPLETED_HEALTH_UNKNOWN

    def observe(self, item):
        self.assert_attempt(item)
        return RuntimeSnapshot(
            self.state,
            item.deployment_id if self.state != RuntimeState.NOT_STARTED else None,
            item.execution_attempt_id
            if self.state != RuntimeState.NOT_STARTED
            else None,
            item.fencing_epoch if self.state != RuntimeState.NOT_STARTED else None,
            item.intent_hash if self.state != RuntimeState.NOT_STARTED else None,
            {},
            "cursor",
            self.client_active,
            self.transitional,
            datetime.now(UTC),
        )

    def events_between(self, _first, _second):
        return self.events

    def verify_health(self, item, *, deadline):
        self.assert_attempt(item)
        checks = {
            name: True for name in required_health_checks(("api",))
        }
        self.state = RuntimeState.HEALTHY
        return HealthCycle(
            checks,
            item.artifact_digest,
            item.target_sha,
            item.expected_migration_revision,
            datetime.now(UTC),
            {"api": 0},
        )

    def assert_attempt(self, item):
        if item != self.item:
            raise AssertionError("caller-forged attempt")


class ProductionSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.item = attempt()
        self.authority = Authority(self.item)
        self.runtime = Runtime(self.item)
        self.supervisor = ProductionDeploymentSupervisor(
            authority=self.authority,
            runtime=self.runtime,
            journal_root=root / "journal",
            lock_path=root / "supervisor.lock",
            sleep=lambda _seconds: None,
            resource_collector=Collector(resource_result(self.item)),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_only_one_concurrent_execution_mutates(self) -> None:
        self.runtime.block_submit = True
        result = []

        def first():
            result.append(
                self.supervisor.execute(self.item.execution_attempt_id)
            )

        thread = threading.Thread(target=first)
        thread.start()
        self.assertTrue(self.runtime.submit_started.wait(timeout=2))
        with self.assertRaises(SupervisorError) as conflict:
            self.supervisor.execute(self.item.execution_attempt_id)
        self.assertEqual(conflict.exception.code, "host_execution_lock_busy")
        self.runtime.submit_release.set()
        thread.join(timeout=3)
        self.assertEqual(result, [RuntimeState.HEALTHY])
        self.assertEqual(self.runtime.submissions, 1)

    def test_lost_acknowledgement_reconciles_without_reissue(self) -> None:
        journal = DurableExecutionJournal(
            Path(self.temporary.name) / "journal",
            self.item.execution_attempt_id,
        )
        journal.append(JournalEvent.ATTEMPT_ACCEPTED, self.item)
        journal.append(JournalEvent.MUTATION_SUBMITTED, self.item)
        self.runtime.state = RuntimeState.MUTATION_COMPLETED_HEALTH_UNKNOWN
        state = self.supervisor.execute(self.item.execution_attempt_id)
        self.assertEqual(state, RuntimeState.HEALTHY)
        self.assertEqual(self.runtime.submissions, 0)

    def test_unknown_and_db_disconnect_fail_closed(self) -> None:
        journal = DurableExecutionJournal(
            Path(self.temporary.name) / "journal",
            self.item.execution_attempt_id,
        )
        journal.append(JournalEvent.ATTEMPT_ACCEPTED, self.item)
        journal.append(JournalEvent.MUTATION_SUBMITTED, self.item)
        self.runtime.state = RuntimeState.UNKNOWN
        self.assertEqual(
            self.supervisor.execute(self.item.execution_attempt_id),
            RuntimeState.UNKNOWN,
        )
        self.assertEqual(self.runtime.submissions, 0)

        second = attempt()
        authority = Authority(second)
        authority.record_resource = lambda _result: (_ for _ in ()).throw(
            OSError("database unavailable")
        )
        supervisor = ProductionDeploymentSupervisor(
            authority=authority,
            runtime=Runtime(second),
            journal_root=Path(self.temporary.name) / "journal-2",
            lock_path=Path(self.temporary.name) / "lock-2",
            resource_collector=Collector(resource_result(second)),
        )
        self.assertEqual(
            supervisor.execute(second.execution_attempt_id),
            RuntimeState.UNKNOWN,
        )

    def test_takeover_requires_stable_quiescence(self) -> None:
        self.runtime.client_active = True
        self.assertFalse(
            self.supervisor.prove_quiescent_takeover(
                self.item.execution_attempt_id
            )
        )
        self.runtime.client_active = False
        self.runtime.state = RuntimeState.FAILED
        self.assertTrue(
            self.supervisor.prove_quiescent_takeover(
                self.item.execution_attempt_id
            )
        )
        self.runtime.events = True
        self.assertFalse(
            self.supervisor.prove_quiescent_takeover(
                self.item.execution_attempt_id
            )
        )

    def test_journal_corruption_and_binding_mismatch_fail_closed(self) -> None:
        journal = DurableExecutionJournal(
            Path(self.temporary.name) / "journal",
            self.item.execution_attempt_id,
        )
        journal.path.write_text("not-json\n", encoding="utf-8")
        with self.assertRaises(SupervisorError) as corrupt:
            self.supervisor.execute(self.item.execution_attempt_id)
        self.assertEqual(corrupt.exception.code, "journal_corrupt")

    def test_journal_records_complete_terminal_sequence(self) -> None:
        self.assertEqual(
            self.supervisor.execute(self.item.execution_attempt_id),
            RuntimeState.HEALTHY,
        )
        journal = DurableExecutionJournal(
            Path(self.temporary.name) / "journal",
            self.item.execution_attempt_id,
        )
        records = journal.load(self.item)
        events = [record["event"] for record in records]
        self.assertEqual(
            events,
            [
                "ATTEMPT_ACCEPTED",
                "MUTATION_SUBMITTED",
                "MUTATION_COMPLETED",
                "HEALTH_STARTED",
                "HEALTH_PASSED",
                "ATTEMPT_TERMINAL",
            ],
        )
        for record in records:
            json.dumps(record)

    def test_mutation_submitted_marker_recorded_before_submit(self) -> None:
        self.assertEqual(
            self.supervisor.execute(self.item.execution_attempt_id),
            RuntimeState.HEALTHY,
        )
        self.assertIn(
            self.item.execution_attempt_id, self.authority.mutation_markers
        )
        self.assertEqual(self.runtime.submissions, 1)

    def test_journal_missing_after_db_submission_fails_closed(self) -> None:
        # Simulate a crash after the DB marker was persisted but before the
        # journal file survived: a fresh supervisor must refuse to mutate.
        self.authority.mutation_markers.append(
            self.item.execution_attempt_id
        )
        with self.assertRaises(SupervisorError) as blocked:
            self.supervisor.execute(self.item.execution_attempt_id)
        self.assertEqual(
            blocked.exception.code, "journal_missing_after_db_submission"
        )
        self.assertEqual(self.runtime.submissions, 0)


if __name__ == "__main__":
    unittest.main()
