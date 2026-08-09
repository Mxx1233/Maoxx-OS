"""Tests for the canonical identity authority (H-3 coverage)."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.services.identity_authority import (
    IdentityAuthorityError,
    assign_feishu_identity,
    provision_principal,
)


class FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def scalar_one(self):
        return "2026-08-09T12:00:00Z"


def fake_db(*, principal=None, current_mapping=None):
    db = MagicMock()
    db.get.return_value = principal
    db.execute.return_value = FakeResult(current_mapping)
    return db


class ProvisionPrincipalTests(unittest.TestCase):
    def test_rejects_unknown_principal_type(self) -> None:
        with self.assertRaises(IdentityAuthorityError) as caught:
            provision_principal(
                MagicMock(),
                principal_type="ROBOT",
                identity_key="k",
                user_id=None,
            )
        self.assertEqual(caught.exception.args[0], "invalid_principal_type")

    def test_human_requires_user_id(self) -> None:
        with self.assertRaises(IdentityAuthorityError) as caught:
            provision_principal(
                MagicMock(),
                principal_type="HUMAN",
                identity_key="k",
                user_id=None,
            )
        self.assertEqual(
            caught.exception.args[0], "invalid_principal_user_binding"
        )

    def test_service_forbids_user_id(self) -> None:
        with self.assertRaises(IdentityAuthorityError) as caught:
            provision_principal(
                MagicMock(),
                principal_type="SERVICE",
                identity_key="k",
                user_id=uuid4(),
            )
        self.assertEqual(
            caught.exception.args[0], "invalid_principal_user_binding"
        )

    def test_provisions_human_principal(self) -> None:
        db = MagicMock()
        principal = provision_principal(
            db,
            principal_type="HUMAN",
            identity_key="maoxx",
            user_id=uuid4(),
        )
        db.add.assert_called_once()
        db.commit.assert_called_once()
        db.refresh.assert_called_once()
        self.assertEqual(principal.principal_type, "HUMAN")
        self.assertEqual(principal.identity_key, "maoxx")


class AssignFeishuIdentityTests(unittest.TestCase):
    def test_requires_human_principal(self) -> None:
        service = SimpleNamespace(principal_type="SERVICE")
        db = fake_db(principal=service)
        with self.assertRaises(IdentityAuthorityError) as caught:
            assign_feishu_identity(
                db,
                tenant_id="t",
                open_id="o",
                principal_id=uuid4(),
                audit_event_id=uuid4(),
            )
        self.assertEqual(caught.exception.args[0], "human_principal_required")

    def test_creates_first_mapping_version_one(self) -> None:
        principal = SimpleNamespace(principal_type="HUMAN")
        db = fake_db(principal=principal, current_mapping=None)
        mapping = assign_feishu_identity(
            db,
            tenant_id="tenant-a",
            open_id="open-1",
            principal_id=uuid4(),
            audit_event_id=uuid4(),
        )
        db.add.assert_called_once()
        db.commit.assert_called_once()
        self.assertEqual(mapping.mapping_version, 1)
        self.assertIsNone(mapping.valid_to)
        self.assertEqual(mapping.provider, "feishu")

    def test_same_mapping_is_idempotent(self) -> None:
        principal_id = uuid4()
        current = SimpleNamespace(
            principal_id=principal_id,
            mapping_version=3,
            valid_to=None,
        )
        principal = SimpleNamespace(principal_type="HUMAN")
        db = fake_db(principal=principal, current_mapping=current)
        mapping = assign_feishu_identity(
            db,
            tenant_id="t",
            open_id="o",
            principal_id=principal_id,
            audit_event_id=uuid4(),
        )
        self.assertIs(mapping, current)
        db.commit.assert_not_called()

    def test_reassignment_closes_old_and_bumps_version(self) -> None:
        old_principal_id = uuid4()
        current = SimpleNamespace(
            principal_id=old_principal_id,
            mapping_version=2,
            valid_to=None,
        )
        principal = SimpleNamespace(principal_type="HUMAN")
        db = fake_db(principal=principal, current_mapping=current)
        new_principal_id = uuid4()
        mapping = assign_feishu_identity(
            db,
            tenant_id="t",
            open_id="o",
            principal_id=new_principal_id,
            audit_event_id=uuid4(),
        )
        self.assertIsNotNone(current.valid_to)
        self.assertEqual(mapping.mapping_version, 3)
        self.assertEqual(mapping.principal_id, new_principal_id)
        db.flush.assert_called_once()

    def test_conflict_rolls_back(self) -> None:
        principal = SimpleNamespace(principal_type="HUMAN")
        db = fake_db(principal=principal, current_mapping=None)
        from sqlalchemy.exc import IntegrityError

        db.commit.side_effect = IntegrityError(
            "INSERT", {}, Exception("duplicate")
        )
        with self.assertRaises(IdentityAuthorityError) as caught:
            assign_feishu_identity(
                db,
                tenant_id="t",
                open_id="o",
                principal_id=uuid4(),
                audit_event_id=uuid4(),
            )
        self.assertEqual(
            caught.exception.args[0], "identity_assignment_conflict"
        )
        db.rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
