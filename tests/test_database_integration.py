import os
import subprocess
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.models import ApprovalRequest, User
from app.services.approval_service import (
    ApprovalCommand,
    create_approval_request,
    process_approval_card_action,
    record_approval_decision,
)


@unittest.skipUnless(
    os.getenv("RUN_DATABASE_INTEGRATION_TESTS") == "1",
    "set RUN_DATABASE_INTEGRATION_TESTS=1 for isolated PostgreSQL tests",
)
class DatabaseIntegrationTests(unittest.TestCase):
    def test_clean_database_upgrades_to_head_without_drift(self) -> None:
        source_url = make_url(os.environ["DATABASE_URL"])
        database_name = f"maoxx_test_{uuid4().hex}"
        self.assertTrue(database_name.startswith("maoxx_test_"))

        admin_kwargs = {
            "host": source_url.host,
            "port": source_url.port,
            "user": source_url.username,
            "password": source_url.password,
            "dbname": "postgres",
            "autocommit": True,
        }
        test_url = source_url.set(database=database_name).render_as_string(
            hide_password=False
        )
        test_env = os.environ.copy()
        test_env["DATABASE_URL"] = test_url

        with psycopg.connect(**admin_kwargs) as connection:
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(
                    sql.Identifier(database_name)
                )
            )

        try:
            for command in (
                ["alembic", "upgrade", "head"],
                ["alembic", "current"],
                ["alembic", "heads"],
                ["alembic", "check"],
            ):
                subprocess.run(
                    command,
                    check=True,
                    env=test_env,
                    capture_output=True,
                    text=True,
                )

            engine = create_engine(test_url)
            try:
                with engine.connect() as connection:
                    revision = connection.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    indexes = set(
                        connection.execute(
                            text(
                                "SELECT indexname FROM pg_indexes "
                                "WHERE schemaname = 'core'"
                            )
                        ).scalars()
                    )
                    constraints = set(
                        connection.execute(
                            text(
                                "SELECT constraint_name "
                                "FROM information_schema.table_constraints "
                                "WHERE table_schema = 'core'"
                            )
                        ).scalars()
                    )
                    triggers = set(
                        connection.execute(
                            text(
                                "SELECT trigger_name "
                                "FROM information_schema.triggers "
                                "WHERE event_object_schema = 'core'"
                            )
                        ).scalars()
                    )

                session_factory = sessionmaker(
                    bind=engine,
                    expire_on_commit=False,
                )
                user_id = uuid4()
                with session_factory() as session:
                    session.add(
                        User(
                            id=user_id,
                            display_name="Integration Test User",
                        )
                    )
                    session.commit()

                with session_factory() as session:
                    approve_request = create_approval_request(
                        session,
                        user_id=user_id,
                        action_code="merge_pr",
                        repository="Example/Repository",
                        pull_request_number=14,
                        target_sha="a" * 40,
                        target_environment="staging",
                        idempotency_key="approve-request",
                        ttl_seconds=1800,
                    )
                    same_request = create_approval_request(
                        session,
                        user_id=user_id,
                        action_code="merge_pr",
                        repository="Example/Repository",
                        pull_request_number=14,
                        target_sha="a" * 40,
                        target_environment="staging",
                        idempotency_key="approve-request",
                        ttl_seconds=1800,
                    )
                self.assertEqual(same_request.id, approve_request.id)

                with session_factory() as session:
                    wait_request = create_approval_request(
                        session,
                        user_id=user_id,
                        action_code="development_plan",
                        repository="Example/Repository",
                        pull_request_number=None,
                        target_sha="f" * 40,
                        target_environment="development",
                        idempotency_key="wait-request",
                        ttl_seconds=1800,
                    )
                    original_expiry = wait_request.expires_at
                with session_factory() as session:
                    waiting = process_approval_card_action(
                        session,
                        action="wait",
                        request_id=wait_request.id,
                        tenant_key="tenant-test",
                        operator_open_id="fake-approver-id",
                        chat_id="chat-test",
                        feishu_event_id="event-wait",
                        actor_user_id=user_id,
                        allowed_tenant_keys=frozenset({"tenant-test"}),
                        allowed_open_ids=frozenset({"fake-approver-id"}),
                        approver_open_ids=frozenset({"fake-approver-id"}),
                        supervision_chat_id="chat-test",
                    )
                self.assertEqual(waiting.code, "waiting")
                with session_factory() as session:
                    persisted_wait_request = session.get(
                        ApprovalRequest,
                        wait_request.id,
                    )
                    wait_decision_count = session.execute(
                        text(
                            "SELECT count(*) FROM core.approval_decisions "
                            "WHERE request_id = :request_id"
                        ),
                        {"request_id": wait_request.id},
                    ).scalar_one()
                self.assertEqual(wait_decision_count, 0)
                self.assertEqual(
                    persisted_wait_request.expires_at,
                    original_expiry,
                )

                with session_factory() as session:
                    approved = process_approval_card_action(
                        session,
                        action="approve",
                        request_id=approve_request.id,
                        tenant_key="tenant-test",
                        operator_open_id="fake-approver-id",
                        chat_id="chat-test",
                        feishu_event_id="event-approve",
                        actor_user_id=user_id,
                        allowed_tenant_keys=frozenset({"tenant-test"}),
                        allowed_open_ids=frozenset({"fake-approver-id"}),
                        approver_open_ids=frozenset({"fake-approver-id"}),
                        supervision_chat_id="chat-test",
                    )
                self.assertEqual(approved.code, "recorded")
                self.assertEqual(approved.decision_code, "approved")

                with session_factory() as session:
                    duplicate = process_approval_card_action(
                        session,
                        action="approve",
                        request_id=approve_request.id,
                        tenant_key="tenant-test",
                        operator_open_id="fake-approver-id",
                        chat_id="chat-test",
                        feishu_event_id="event-approve",
                        actor_user_id=user_id,
                        allowed_tenant_keys=frozenset({"tenant-test"}),
                        allowed_open_ids=frozenset({"fake-approver-id"}),
                        approver_open_ids=frozenset({"fake-approver-id"}),
                        supervision_chat_id="chat-test",
                    )
                self.assertEqual(duplicate.code, "duplicate")

                with session_factory() as session:
                    conflicting = process_approval_card_action(
                        session,
                        action="reject",
                        request_id=approve_request.id,
                        tenant_key="tenant-test",
                        operator_open_id="fake-approver-id",
                        chat_id="chat-test",
                        feishu_event_id="event-conflicting",
                        actor_user_id=user_id,
                        allowed_tenant_keys=frozenset({"tenant-test"}),
                        allowed_open_ids=frozenset({"fake-approver-id"}),
                        approver_open_ids=frozenset({"fake-approver-id"}),
                        supervision_chat_id="chat-test",
                    )
                self.assertEqual(conflicting.code, "already_decided")

                with session_factory() as session:
                    reject_request = create_approval_request(
                        session,
                        user_id=user_id,
                        action_code="production_deploy",
                        repository="Example/Repository",
                        pull_request_number=14,
                        target_sha="b" * 40,
                        target_environment="production",
                        idempotency_key="reject-request",
                        ttl_seconds=1800,
                    )
                with session_factory() as session:
                    rejected = process_approval_card_action(
                        session,
                        action="reject",
                        request_id=reject_request.id,
                        tenant_key="tenant-test",
                        operator_open_id="fake-approver-id",
                        chat_id="chat-test",
                        feishu_event_id="event-reject",
                        actor_user_id=user_id,
                        allowed_tenant_keys=frozenset({"tenant-test"}),
                        allowed_open_ids=frozenset({"fake-approver-id"}),
                        approver_open_ids=frozenset({"fake-approver-id"}),
                        supervision_chat_id="chat-test",
                    )
                self.assertEqual(rejected.code, "recorded")
                self.assertEqual(rejected.decision_code, "rejected")

                with session_factory() as session:
                    expired_request = ApprovalRequest(
                        user_id=user_id,
                        action_code="development_plan",
                        repository="Example/Repository",
                        target_sha="c" * 40,
                        target_environment="development",
                        idempotency_key="expired-request",
                        requested_at=datetime.now(UTC) - timedelta(hours=2),
                        expires_at=datetime.now(UTC) - timedelta(hours=1),
                    )
                    session.add(expired_request)
                    session.commit()
                with session_factory() as session:
                    expired = process_approval_card_action(
                        session,
                        action="approve",
                        request_id=expired_request.id,
                        tenant_key="tenant-test",
                        operator_open_id="fake-approver-id",
                        chat_id="chat-test",
                        feishu_event_id="event-expired",
                        actor_user_id=user_id,
                        allowed_tenant_keys=frozenset({"tenant-test"}),
                        allowed_open_ids=frozenset({"fake-approver-id"}),
                        approver_open_ids=frozenset({"fake-approver-id"}),
                        supervision_chat_id="chat-test",
                    )
                with session_factory() as session:
                    unknown = process_approval_card_action(
                        session,
                        action="approve",
                        request_id=uuid4(),
                        tenant_key="tenant-test",
                        operator_open_id="fake-approver-id",
                        chat_id="chat-test",
                        feishu_event_id="event-unknown",
                        actor_user_id=user_id,
                        allowed_tenant_keys=frozenset({"tenant-test"}),
                        allowed_open_ids=frozenset({"fake-approver-id"}),
                        approver_open_ids=frozenset({"fake-approver-id"}),
                        supervision_chat_id="chat-test",
                    )
                self.assertEqual(expired.code, "expired")
                self.assertEqual(unknown.code, "unknown_request")

                with session_factory() as session:
                    lock_expiry_request = create_approval_request(
                        session,
                        user_id=user_id,
                        action_code="production_deploy",
                        repository="Example/Repository",
                        pull_request_number=16,
                        target_sha="e" * 40,
                        target_environment="production",
                        idempotency_key="lock-expiry-request",
                        ttl_seconds=3,
                    )

                lock_holder = session_factory()
                lock_holder.execute(
                    text(
                        "SELECT id FROM core.approval_requests "
                        "WHERE id = :request_id FOR UPDATE"
                    ),
                    {"request_id": lock_expiry_request.id},
                ).scalar_one()
                decision_started = threading.Event()
                lock_expiry_outcomes: list[str] = []
                worker_details: dict[str, object] = {}

                def decide_after_lock_wait() -> None:
                    with session_factory() as session:
                        worker_details["transaction_started_at"] = (
                            session.execute(
                                text("SELECT CURRENT_TIMESTAMP")
                            ).scalar_one()
                        )
                        worker_details["backend_pid"] = session.execute(
                            text("SELECT pg_backend_pid()")
                        ).scalar_one()
                        decision_started.set()
                        outcome = record_approval_decision(
                            session,
                            command=ApprovalCommand(
                                "approved",
                                lock_expiry_request.id,
                            ),
                            feishu_event_id="event-lock-expiry",
                            actor_user_id=user_id,
                            actor_open_id="fake-approver-id",
                        )
                        lock_expiry_outcomes.append(outcome.code)

                lock_wait_thread = threading.Thread(
                    target=decide_after_lock_wait
                )
                lock_wait_thread.start()
                self.assertTrue(decision_started.wait(timeout=5))
                self.assertLess(
                    worker_details["transaction_started_at"],
                    lock_expiry_request.expires_at,
                )

                lock_observed = False
                for _attempt in range(50):
                    with session_factory() as observer:
                        wait_event_type = observer.execute(
                            text(
                                "SELECT wait_event_type FROM pg_stat_activity "
                                "WHERE pid = :pid"
                            ),
                            {"pid": worker_details["backend_pid"]},
                        ).scalar_one_or_none()
                    if wait_event_type == "Lock":
                        lock_observed = True
                        break
                    time.sleep(0.02)
                self.assertTrue(lock_observed)

                while True:
                    with session_factory() as observer:
                        wall_clock = observer.execute(
                            text("SELECT clock_timestamp()")
                        ).scalar_one()
                    if wall_clock >= lock_expiry_request.expires_at:
                        break
                    time.sleep(0.02)

                lock_holder.commit()
                lock_holder.close()
                lock_wait_thread.join(timeout=10)
                self.assertFalse(lock_wait_thread.is_alive())
                self.assertEqual(lock_expiry_outcomes, ["expired"])
                with session_factory() as session:
                    persisted_after_expiry = session.execute(
                        text(
                            "SELECT count(*) FROM core.approval_decisions "
                            "WHERE request_id = :request_id"
                        ),
                        {"request_id": lock_expiry_request.id},
                    ).scalar_one()
                self.assertEqual(persisted_after_expiry, 0)

                with session_factory() as session:
                    concurrent_request = create_approval_request(
                        session,
                        user_id=user_id,
                        action_code="production_deploy",
                        repository="Example/Repository",
                        pull_request_number=15,
                        target_sha="d" * 40,
                        target_environment="production",
                        idempotency_key="concurrent-request",
                        ttl_seconds=1800,
                    )
                barrier = threading.Barrier(2)
                outcomes: list[str] = []

                def decide(decision_code: str, event_id: str) -> None:
                    with session_factory() as session:
                        barrier.wait()
                        outcome = record_approval_decision(
                            session,
                            command=ApprovalCommand(
                                decision_code,
                                concurrent_request.id,
                            ),
                            feishu_event_id=event_id,
                            actor_user_id=user_id,
                            actor_open_id="fake-approver-id",
                        )
                        outcomes.append(outcome.code)

                threads = [
                    threading.Thread(
                        target=decide,
                        args=("approved", "event-concurrent-a"),
                    ),
                    threading.Thread(
                        target=decide,
                        args=("rejected", "event-concurrent-b"),
                    ),
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=10)
                    self.assertFalse(thread.is_alive())
                self.assertEqual(
                    sorted(outcomes),
                    ["already_decided", "recorded"],
                )

                with session_factory() as session:
                    decision_id = session.execute(
                        text(
                            "SELECT id FROM core.approval_decisions "
                            "ORDER BY decided_at LIMIT 1"
                        )
                    ).scalar_one()
                    with self.assertRaises(DBAPIError):
                        session.execute(
                            text(
                                "UPDATE core.approval_decisions "
                                "SET decision_code = 'rejected' WHERE id = :id"
                            ),
                            {"id": decision_id},
                        )
                        session.commit()
                    session.rollback()
                    with self.assertRaises(DBAPIError):
                        session.execute(
                            text(
                                "DELETE FROM core.approval_decisions "
                                "WHERE id = :id"
                            ),
                            {"id": decision_id},
                        )
                        session.commit()
                    session.rollback()
            finally:
                engine.dispose()

            self.assertEqual(
                revision,
                "0003_phase_1d_f_deployment",
            )
            self.assertIn("ix_entities_user_type_status", indexes)
            self.assertIn("ix_raw_inputs_user_received", indexes)
            self.assertIn("uq_raw_inputs_channel_message", constraints)
            self.assertIn(
                "uq_approval_requests_idempotency_key",
                constraints,
            )
            self.assertIn("uq_approval_decisions_request_id", constraints)
            self.assertIn(
                "uq_approval_decisions_feishu_event_id",
                constraints,
            )
            self.assertIn(
                "ck_approval_requests_action_target",
                constraints,
            )
            self.assertIn(
                "ck_approval_requests_target_sha",
                constraints,
            )
            self.assertIn(
                "ck_approval_decisions_identity_fingerprint",
                constraints,
            )
            self.assertIn(
                "ix_approval_requests_user_requested",
                indexes,
            )
            self.assertIn("ix_approval_requests_target", indexes)
            self.assertIn(
                "ix_approval_decisions_actor_decided",
                indexes,
            )
            self.assertIn(
                "trg_approval_decisions_append_only",
                triggers,
            )
            self.assertIn(
                "trg_deployment_state_events_append_only",
                triggers,
            )
            self.assertIn(
                "trg_deployment_evidence_append_only",
                triggers,
            )
        finally:
            with psycopg.connect(**admin_kwargs) as connection:
                connection.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                        sql.Identifier(database_name)
                    )
                )


if __name__ == "__main__":
    unittest.main()
