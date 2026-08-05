import unittest

from sqlalchemy import Index, UniqueConstraint

from app.models import Entity, RawInput


class CoreModelMetadataTests(unittest.TestCase):
    def test_entity_uses_only_migration_0001_composite_index(self) -> None:
        indexes = {
            index.name: tuple(column.name for column in index.columns)
            for index in Entity.__table__.indexes
        }

        self.assertEqual(
            indexes,
            {
                "ix_entities_user_type_status": (
                    "user_id",
                    "entity_type",
                    "status_code",
                )
            },
        )

    def test_raw_input_uses_only_migration_0001_composite_index(self) -> None:
        indexes = {
            index.name: tuple(column.name for column in index.columns)
            for index in RawInput.__table__.indexes
            if isinstance(index, Index)
        }

        self.assertEqual(
            indexes,
            {
                "ix_raw_inputs_user_received": (
                    "user_id",
                    "received_at",
                )
            },
        )

    def test_raw_input_declares_message_deduplication_constraint(self) -> None:
        unique_constraints = {
            constraint.name: tuple(
                column.name for column in constraint.columns
            )
            for constraint in RawInput.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        }

        self.assertEqual(
            unique_constraints["uq_raw_inputs_channel_message"],
            ("channel_code", "external_message_id"),
        )


if __name__ == "__main__":
    unittest.main()
