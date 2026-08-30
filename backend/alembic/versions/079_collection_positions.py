"""allow reporting Collections to include Position ledger entries

Revision ID: 079
Revises: 078
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "079"
down_revision: Union[str, None] = "078"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "collection_positions",
        sa.Column(
            "collection_id",
            sa.UUID(),
            sa.ForeignKey("collections.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "position_id",
            sa.UUID(),
            sa.ForeignKey("positions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("collection_positions")
