"""make correction batch commits idempotent

Revision ID: 080
Revises: 079
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "080"
down_revision: Union[str, None] = "079"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_correction_batch_digest",
        "correction_batches",
        ["workspace_id", "operation", "digest"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_correction_batch_digest", "correction_batches", type_="unique")
