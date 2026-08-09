"""Trusted Production PostgreSQL backup and canary-restore authority."""

from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy import select

from app.models import DeploymentEvidence, StagingAcceptance


BACKUP_PRODUCER = "maoxx-production-backup-authority"


class BackupAuthorityError(RuntimeError):
    pass


class ProductionBackupAuthority:
    """Creates and verifies one deployment-bound custom-format archive."""

    def __init__(
        self,
        *,
        session_factory,
        backup_root: Path,
        pg_service: str = "maoxx_production",
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = session_factory
        self._root = backup_root
        self._pg_service = pg_service
        self._run = run
        self._now = now

    def create_and_verify(self, deployment_id: UUID) -> DeploymentEvidence:
        with self._sessions() as db:
            acceptance = db.execute(
                select(StagingAcceptance).where(
                    StagingAcceptance.deployment_id == deployment_id
                )
            ).scalar_one_or_none()
            if acceptance is None:
                raise BackupAuthorityError("missing_staging_acceptance")
            if acceptance.deployment_id != deployment_id:
                raise BackupAuthorityError("wrong_deployment_binding")
            accepted_at = acceptance.completed_at
        operation_id = uuid4()
        created_at = self._now()
        if created_at < accepted_at:
            raise BackupAuthorityError("backup_precedes_staging_acceptance")
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        archive = self._root / f"{deployment_id}-{operation_id}.dump"
        dump = self._command(
            "pg_dump",
            f"--dbname=service={self._pg_service}",
            "--format=custom",
            "--file",
            str(archive),
        )
        if (
            dump.returncode != 0
            or not archive.is_file()
            or archive.stat().st_size <= 0
        ):
            raise BackupAuthorityError("backup_creation_failed")
        checksum = self._sha256(archive)
        catalog = self._command("pg_restore", "--list", str(archive))
        required = {
            "SCHEMA - core",
            "TABLE core approval_requests",
            "TABLE core approval_decisions",
        }
        if catalog.returncode != 0 or any(
            item not in catalog.stdout for item in required
        ):
            raise BackupAuthorityError("backup_catalog_invalid")
        canary_db = f"maoxx_restore_canary_{operation_id.hex}"
        restored = False
        try:
            if (
                self._command(
                    "createdb",
                    f"--maintenance-db=service={self._pg_service}",
                    canary_db,
                ).returncode
                != 0
            ):
                raise BackupAuthorityError("canary_database_create_failed")
            restore = self._command(
                "pg_restore",
                f"--dbname=service={self._pg_service} dbname={canary_db}",
                "--schema=core",
                "--table=approval_requests",
                "--table=approval_decisions",
                str(archive),
            )
            probe = self._command(
                "psql",
                f"--dbname=service={self._pg_service} dbname={canary_db}",
                "--tuples-only",
                "--command",
                "SELECT to_regclass('core.approval_requests') IS NOT NULL "
                "AND to_regclass('core.approval_decisions') IS NOT NULL",
            )
            restored = (
                restore.returncode == 0
                and probe.returncode == 0
                and "t" in probe.stdout
            )
            if not restored:
                raise BackupAuthorityError("canary_restore_failed")
        finally:
            self._command(
                "dropdb",
                f"--maintenance-db=service={self._pg_service}",
                "--if-exists",
                canary_db,
            )
        verified_at = self._now()
        if self._sha256(archive) != checksum:
            raise BackupAuthorityError("backup_checksum_changed")
        if verified_at - created_at > timedelta(minutes=30):
            raise BackupAuthorityError("backup_verification_stale")
        with self._sessions() as db:
            evidence = DeploymentEvidence(
                deployment_id=deployment_id,
                evidence_type="predeploy_backup",
                evidence_key=str(operation_id),
                status_code="passed",
                occurred_at=verified_at,
                payload={
                    "backup_operation_id": str(operation_id),
                    "deployment_id": str(deployment_id),
                    "environment": "production",
                    "persistent_state_scope": "postgresql:core",
                    "artifact_path": str(archive),
                    "size_bytes": archive.stat().st_size,
                    "sha256": checksum,
                    "backup_format": "postgresql-custom",
                    "catalog_validated": True,
                    "canary_restore_validated": restored,
                    "created_at": created_at.isoformat(),
                    "verified_at": verified_at.isoformat(),
                    "producer_identity": BACKUP_PRODUCER,
                },
            )
            db.add(evidence)
            db.commit()
            db.refresh(evidence)
            return evidence

    def _command(self, *argv: str) -> subprocess.CompletedProcess[str]:
        return self._run(
            list(argv),
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
