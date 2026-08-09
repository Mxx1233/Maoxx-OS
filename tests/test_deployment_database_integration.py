import os
import subprocess
import unittest
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import (
    DeploymentEvidence,
    DeploymentIntent,
    ExternalIdentity,
    Principal,
    User,
)
from app.services.deployment_authority import external_identity_fingerprint


@unittest.skipUnless(
    os.getenv("RUN_DATABASE_INTEGRATION_TESTS") == "1",
    "set RUN_DATABASE_INTEGRATION_TESTS=1 for isolated PostgreSQL tests",
)
class DeploymentDatabaseIntegrationTests(unittest.TestCase):
    """Schema-level authority guarantees on a disposable PostgreSQL database."""

    @classmethod
    def setUpClass(cls) -> None:
        source_url = make_url(os.environ["DATABASE_URL"])
        cls.database_name = f"maoxx_authority_test_{uuid4().hex}"
        cls.admin_kwargs = {
            "host": source_url.host,
            "port": source_url.port,
            "user": source_url.username,
            "password": source_url.password,
            "dbname": "postgres",
            "autocommit": True,
        }
        cls.test_url = source_url.set(
            database=cls.database_name
        ).render_as_string(hide_password=False)
        with psycopg.connect(**cls.admin_kwargs) as connection:
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(
                    sql.Identifier(cls.database_name)
                )
            )
        migration_environment = os.environ | {"DATABASE_URL": cls.test_url}
        for command in (
            ["alembic", "upgrade", "head"],
            ["alembic", "current"],
            ["alembic", "heads"],
            ["alembic", "check"],
        ):
            subprocess.run(
                command,
                check=True,
                env=migration_environment,
                capture_output=True,
            )
        cls.engine = create_engine(cls.test_url)
        cls.sessions = sessionmaker(bind=cls.engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()
        with psycopg.connect(**cls.admin_kwargs) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(cls.database_name)
                )
            )

    def _principal_and_mapping(self):
        user_id = uuid4()
        principal_id = uuid4()
        tenant = external_identity_fingerprint("tenant:test")
        subject = external_identity_fingerprint(f"subject:{uuid4()}")
        with self.sessions() as database:
            database.add(User(id=user_id, display_name="Authority test"))
            database.flush()
            principal = Principal(
                id=principal_id,
                principal_type="HUMAN",
                user_id=user_id,
                identity_key=f"human:{user_id}",
            )
            mapping = ExternalIdentity(
                provider="feishu",
                tenant_fingerprint=tenant,
                subject_fingerprint=subject,
                principal_id=principal_id,
                mapping_version=1,
                valid_from=datetime.now(UTC),
                audit_event_id=uuid4(),
            )
            database.add_all((principal, mapping))
            database.commit()
            return user_id, principal_id, mapping.id, tenant, subject

    def _intent(self, user_id, principal_id, mapping_id):
        intent = DeploymentIntent(
            intent_kind="deploy",
            repository="Mxx1233/Maoxx-OS",
            target_sha="a" * 40,
            target_environment="production",
            action_code="production_deploy",
            artifact_digest="sha256:" + "b" * 64,
            service_set=["api"],
            migration_risk="NONE",
            config_fingerprint="sha256:" + "c" * 64,
            user_id=user_id,
            requester_principal_id=principal_id,
            requester_external_identity_id=mapping_id,
            requested_by_identity_fingerprint="d" * 64,
            idempotency_key=f"intent-{uuid4()}",
        )
        with self.sessions() as database:
            database.add(intent)
            database.commit()
            database.refresh(intent)
            return intent

    def test_external_identity_is_unique_versioned_and_historical(self) -> None:
        _, principal_id, mapping_id, tenant, subject = (
            self._principal_and_mapping()
        )
        with self.sessions() as database:
            duplicate = ExternalIdentity(
                provider="feishu",
                tenant_fingerprint=tenant,
                subject_fingerprint=subject,
                principal_id=principal_id,
                mapping_version=2,
                audit_event_id=uuid4(),
            )
            database.add(duplicate)
            with self.assertRaises(IntegrityError):
                database.commit()
            database.rollback()

        with self.sessions() as database:
            database.execute(
                text(
                    "UPDATE core.external_identities "
                    "SET valid_to = clock_timestamp() WHERE id = :id"
                ),
                {"id": mapping_id},
            )
            database.commit()
        with self.sessions() as database:
            with self.assertRaises(DBAPIError):
                database.execute(
                    text(
                        "UPDATE core.external_identities "
                        "SET subject_fingerprint = :value WHERE id = :id"
                    ),
                    {"id": mapping_id, "value": "e" * 64},
                )
                database.commit()
            database.rollback()

    def test_evidence_status_contract_and_pending_gate_semantics(self) -> None:
        user_id, principal_id, mapping_id, _, _ = (
            self._principal_and_mapping()
        )
        intent = self._intent(user_id, principal_id, mapping_id)
        with self.sessions() as database:
            for status in ("pending", "passed", "failed", "recorded"):
                database.add(
                    DeploymentEvidence(
                        deployment_id=intent.id,
                        evidence_type="status_contract",
                        evidence_key=f"{status}-{uuid4()}",
                        status_code=status,
                        occurred_at=datetime.now(UTC),
                        payload={"status": status},
                    )
                )
            database.commit()
        with self.sessions() as database:
            database.add(
                DeploymentEvidence(
                    deployment_id=intent.id,
                    evidence_type="status_contract",
                    evidence_key=f"invalid-{uuid4()}",
                    status_code="healthy",
                    occurred_at=datetime.now(UTC),
                    payload={},
                )
            )
            with self.assertRaises(IntegrityError):
                database.commit()
            database.rollback()
        with self.sessions() as database:
            passed = database.scalar(
                text(
                    "SELECT count(*) FROM core.deployment_evidence "
                    "WHERE deployment_id = :deployment_id "
                    "AND evidence_type = 'status_contract' "
                    "AND status_code = 'passed'"
                ),
                {"deployment_id": intent.id},
            )
            self.assertEqual(passed, 1)

    def test_append_only_evidence_rejects_update_and_delete(self) -> None:
        user_id, principal_id, mapping_id, _, _ = (
            self._principal_and_mapping()
        )
        intent = self._intent(user_id, principal_id, mapping_id)
        with self.sessions() as database:
            evidence = DeploymentEvidence(
                deployment_id=intent.id,
                evidence_type="audit",
                evidence_key=f"audit-{uuid4()}",
                status_code="recorded",
                occurred_at=datetime.now(UTC),
                payload={"result": "original"},
            )
            database.add(evidence)
            database.commit()
            evidence_id = evidence.id
        for statement in (
            "UPDATE core.deployment_evidence SET status_code = 'failed' WHERE id = :id",
            "DELETE FROM core.deployment_evidence WHERE id = :id",
        ):
            with self.sessions() as database:
                with self.assertRaises(DBAPIError):
                    database.execute(text(statement), {"id": evidence_id})
                    database.commit()
                database.rollback()


if __name__ == "__main__":
    unittest.main()
