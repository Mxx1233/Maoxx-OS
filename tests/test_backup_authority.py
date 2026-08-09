"""Tests for the trusted production backup authority (H-3 coverage)."""

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.services.backup_authority import (
    BackupAuthorityError,
    ProductionBackupAuthority,
)

CATALOG = """;
; Archive created by pg_dump
;
SCHEMA - core
TABLE core approval_requests
TABLE core approval_decisions
"""


class CommandFixture:
    def __init__(self, *, dump_ok=True, catalog_ok=True, restore_ok=True):
        self.dump_ok = dump_ok
        self.catalog_ok = catalog_ok
        self.restore_ok = restore_ok
        self.calls = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(argv)
        name = argv[0]
        if name == "pg_dump":
            if self.dump_ok:
                target = Path(argv[argv.index("--file") + 1])
                target.write_bytes(b"dummy-pg-dump-content")
            return SimpleNamespace(
                returncode=0 if self.dump_ok else 1, stdout="", stderr=""
            )
        if name == "pg_restore" and "--list" in argv:
            return SimpleNamespace(
                returncode=0 if self.catalog_ok else 1,
                stdout=CATALOG if self.catalog_ok else "",
                stderr="",
            )
        if name == "createdb":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if name == "pg_restore":
            return SimpleNamespace(
                returncode=0 if self.restore_ok else 1, stdout="", stderr=""
            )
        if name == "psql":
            return SimpleNamespace(
                returncode=0 if self.restore_ok else 1,
                stdout=" t" if self.restore_ok else "",
                stderr="",
            )
        if name == "dropdb":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {argv}")


def make_authority(*, command, deployment_id=None, now=None):
    return ProductionBackupAuthority(
        session_factory=session_factory(deployment_id=deployment_id),
        backup_root=Path(tempfile.mkdtemp()),
        run=command,
        now=now or (lambda: datetime.now(UTC)),
    )


def session_factory(*, deployment_id=None):
    session = MagicMock()
    session.__enter__.return_value = session
    acceptance = (
        SimpleNamespace(
            deployment_id=deployment_id,
            completed_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        if deployment_id is not None
        else None
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = acceptance
    session.execute.return_value = result
    return MagicMock(return_value=session)


class BackupAuthorityTests(unittest.TestCase):
    def test_successful_backup_records_passed_evidence(self) -> None:
        deployment_id = uuid4()
        command = CommandFixture()
        authority = make_authority(
            command=command, deployment_id=deployment_id
        )
        evidence = authority.create_and_verify(deployment_id)
        self.assertEqual(evidence.status_code, "passed")
        self.assertEqual(evidence.evidence_type, "predeploy_backup")
        payload = evidence.payload
        self.assertEqual(
            payload["producer_identity"], "maoxx-production-backup-authority"
        )
        self.assertTrue(payload["catalog_validated"])
        self.assertTrue(payload["canary_restore_validated"])
        self.assertIn("sha256", payload)
        self.assertEqual(payload["environment"], "production")
        names = [call[0] for call in command.calls]
        self.assertEqual(
            names,
            [
                "pg_dump",
                "pg_restore",
                "createdb",
                "pg_restore",
                "psql",
                "dropdb",
            ],
        )

    def test_missing_staging_acceptance_fails_closed(self) -> None:
        deployment_id = uuid4()
        authority = make_authority(
            command=CommandFixture(), deployment_id=None
        )
        with self.assertRaises(BackupAuthorityError) as caught:
            authority.create_and_verify(deployment_id)
        self.assertEqual(
            caught.exception.args[0], "missing_staging_acceptance"
        )

    def test_backup_precedes_staging_acceptance_fails_closed(self) -> None:
        deployment_id = uuid4()
        command = CommandFixture()
        past = datetime.now(UTC) + timedelta(minutes=5)  # acceptance in future
        session = MagicMock()
        session.__enter__.return_value = session
        session.execute.return_value.scalar_one_or_none.return_value = (
            SimpleNamespace(deployment_id=deployment_id, completed_at=past)
        )
        authority = ProductionBackupAuthority(
            session_factory=MagicMock(return_value=session),
            backup_root=Path(tempfile.mkdtemp()),
            run=command,
            now=lambda: datetime.now(UTC),
        )
        with self.assertRaises(BackupAuthorityError) as caught:
            authority.create_and_verify(deployment_id)
        self.assertEqual(
            caught.exception.args[0], "backup_precedes_staging_acceptance"
        )

    def test_dump_failure_fails_closed(self) -> None:
        deployment_id = uuid4()
        authority = make_authority(
            command=CommandFixture(dump_ok=False),
            deployment_id=deployment_id,
        )
        with self.assertRaises(BackupAuthorityError) as caught:
            authority.create_and_verify(deployment_id)
        self.assertEqual(caught.exception.args[0], "backup_creation_failed")

    def test_invalid_catalog_fails_closed(self) -> None:
        deployment_id = uuid4()
        authority = make_authority(
            command=CommandFixture(catalog_ok=False),
            deployment_id=deployment_id,
        )
        with self.assertRaises(BackupAuthorityError) as caught:
            authority.create_and_verify(deployment_id)
        self.assertEqual(caught.exception.args[0], "backup_catalog_invalid")

    def test_canary_restore_failure_fails_closed(self) -> None:
        deployment_id = uuid4()
        authority = make_authority(
            command=CommandFixture(restore_ok=False),
            deployment_id=deployment_id,
        )
        with self.assertRaises(BackupAuthorityError) as caught:
            authority.create_and_verify(deployment_id)
        self.assertEqual(caught.exception.args[0], "canary_restore_failed")

    def test_stale_verification_fails_closed(self) -> None:
        deployment_id = uuid4()
        command = CommandFixture()
        start = datetime.now(UTC)
        times = iter(
            [
                start,
                start + timedelta(minutes=31),
                start + timedelta(minutes=31),
            ]
        )
        authority = make_authority(
            command=command,
            deployment_id=deployment_id,
            now=lambda: next(times),
        )
        with self.assertRaises(BackupAuthorityError) as caught:
            authority.create_and_verify(deployment_id)
        self.assertEqual(caught.exception.args[0], "backup_verification_stale")


if __name__ == "__main__":
    unittest.main()
