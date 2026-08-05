"""Create initial core tables.

Revision ID: 0001_core_foundation
Revises:
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0001_core_foundation"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS core")

    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "display_name",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column(
            "timezone",
            sa.String(length=64),
            nullable=False,
            server_default="Europe/Berlin",
        ),
        sa.Column(
            "locale",
            sa.String(length=16),
            nullable=False,
            server_default="zh-CN",
        ),
        sa.Column(
            "default_units",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "privacy_settings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="core",
    )

    op.create_table(
        "entities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "entity_type",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "title",
            sa.String(length=300),
            nullable=True,
        ),
        sa.Column(
            "status_code",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "source_code",
            sa.String(length=64),
            nullable=False,
            server_default="system",
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["core.users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="core",
    )

    op.create_index(
        "ix_entities_user_type_status",
        "entities",
        ["user_id", "entity_type", "status_code"],
        schema="core",
    )

    op.create_table(
        "raw_inputs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "channel_code",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "external_message_id",
            sa.String(length=200),
            nullable=True,
        ),
        sa.Column(
            "input_type",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "text_content",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "processing_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["core.users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_code",
            "external_message_id",
            name="uq_raw_inputs_channel_message",
        ),
        schema="core",
    )

    op.create_index(
        "ix_raw_inputs_user_received",
        "raw_inputs",
        ["user_id", "received_at"],
        schema="core",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_raw_inputs_user_received",
        table_name="raw_inputs",
        schema="core",
    )
    op.drop_table("raw_inputs", schema="core")

    op.drop_index(
        "ix_entities_user_type_status",
        table_name="entities",
        schema="core",
    )
    op.drop_table("entities", schema="core")
    op.drop_table("users", schema="core")

    op.execute("DROP SCHEMA IF EXISTS core")
