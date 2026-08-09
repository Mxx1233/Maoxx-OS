"""Add card_message_id to approval requests for CAS persistence.

Phase 1D-F2 H-8: the interactive approval card message ID must be
persisted after a successful send so cards can be rebuilt from the
authoritative database binding instead of chat history.

Revision ID: 0004_approval_card_message_id
Revises: 0003_phase_1d_f_deployment
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_approval_card_message_id"
down_revision: Union[str, None] = "0003_phase_1d_f_deployment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "approval_requests",
        sa.Column("card_message_id", sa.String(length=200), nullable=True),
        schema="core",
    )


def downgrade() -> None:
    op.drop_column("approval_requests", "card_message_id", schema="core")
