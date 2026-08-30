"""add category provenance and reversible correction batches

Revision ID: 077
Revises: 076
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "077"
down_revision: Union[str, None] = "076"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("category_origin", sa.String(length=20), nullable=True))
    op.add_column("transactions", sa.Column("category_rule_id", sa.UUID(), nullable=True))
    op.create_index("ix_transactions_category_origin", "transactions", ["category_origin"])
    op.create_index("ix_transactions_category_rule_id", "transactions", ["category_rule_id"])
    op.create_foreign_key(
        "fk_transactions_category_rule_id_rules", "transactions", "rules",
        ["category_rule_id"], ["id"], ondelete="SET NULL"
    )
    op.execute("UPDATE transactions SET category_origin = 'legacy' WHERE category_id IS NOT NULL")

    op.create_table(
        "correction_batches",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("workspace_id", sa.UUID(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("operation", sa.String(length=50), nullable=False, server_default="rule_category_apply"),
        sa.Column("digest", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="committed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_correction_batches_workspace_id", "correction_batches", ["workspace_id"])
    op.create_table(
        "correction_batch_items",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("batch_id", sa.UUID(), sa.ForeignKey("correction_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("transaction_id", sa.UUID(), sa.ForeignKey("transactions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("before_state", sa.JSON(), nullable=False),
        sa.Column("after_state", sa.JSON(), nullable=False),
    )
    op.create_index("ix_correction_batch_items_batch_id", "correction_batch_items", ["batch_id"])
    op.create_index("ix_correction_batch_items_transaction_id", "correction_batch_items", ["transaction_id"])

    op.create_table(
        "positions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("side", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("counterparty", sa.String(length=255), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="BRL"),
        sa.Column("original_principal", sa.Numeric(18, 2), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("interest_rate", sa.Numeric(12, 6), nullable=True),
        sa.Column("liquidity", sa.String(length=20), nullable=False, server_default="illiquid"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("valuation_date", sa.Date(), nullable=True),
        sa.Column("valuation_source", sa.String(length=100), nullable=True),
        sa.Column("valuation_confidence", sa.String(length=20), nullable=True),
        sa.Column("group_id", sa.UUID(), sa.ForeignKey("asset_groups.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_positions_workspace_id", "positions", ["workspace_id"])
    op.create_index("ix_positions_group_id", "positions", ["group_id"])
    op.create_index("ix_positions_workspace_active", "positions", ["workspace_id", "is_archived"])
    op.create_table(
        "position_movements",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("position_id", sa.UUID(), sa.ForeignKey("positions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("transaction_id", sa.UUID(), sa.ForeignKey("transactions.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("principal_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("cash_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("interest_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("fee_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("tax_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("fx_rate", sa.Numeric(20, 10), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_position_movements_position_id", "position_movements", ["position_id"])
    op.create_index("ix_position_movements_transaction_id", "position_movements", ["transaction_id"])
    op.create_index("ux_position_movements_idempotency", "position_movements", ["position_id", "idempotency_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_position_movements_idempotency", table_name="position_movements")
    op.drop_index("ix_position_movements_transaction_id", table_name="position_movements")
    op.drop_index("ix_position_movements_position_id", table_name="position_movements")
    op.drop_table("position_movements")
    op.drop_index("ix_positions_workspace_active", table_name="positions")
    op.drop_index("ix_positions_group_id", table_name="positions")
    op.drop_index("ix_positions_workspace_id", table_name="positions")
    op.drop_table("positions")
    op.drop_table("correction_batch_items")
    op.drop_index("ix_correction_batches_workspace_id", table_name="correction_batches")
    op.drop_table("correction_batches")
    op.drop_constraint("fk_transactions_category_rule_id_rules", "transactions", type_="foreignkey")
    op.drop_index("ix_transactions_category_rule_id", table_name="transactions")
    op.drop_index("ix_transactions_category_origin", table_name="transactions")
    op.drop_column("transactions", "category_rule_id")
    op.drop_column("transactions", "category_origin")
