"""cache official IPCA observations for deterministic closes

Revision ID: 083
Revises: 082
"""

from alembic import op
import sqlalchemy as sa


revision = "083"
down_revision = "082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ipca_observations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("rate", sa.Numeric(precision=12, scale=8), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("period", "source", name="uq_ipca_observations_period_source"),
    )
    op.create_index("ix_ipca_observations_period", "ipca_observations", ["period"])


def downgrade() -> None:
    op.drop_index("ix_ipca_observations_period", table_name="ipca_observations")
    op.drop_table("ipca_observations")
