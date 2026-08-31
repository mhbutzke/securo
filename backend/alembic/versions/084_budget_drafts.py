"""mark advisory budgets as drafts

Revision ID: 084
Revises: 083
"""

from alembic import op
import sqlalchemy as sa


revision = "084"
down_revision = "083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("budgets", sa.Column("is_draft", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("budgets", "is_draft")
