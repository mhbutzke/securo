"""Add explicit accounting, liquidity and position valuation metadata.

Revision ID: 081
Revises: 080
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "081"
down_revision: Union[str, None] = "080"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("categories", sa.Column("accounting_role", sa.String(length=24), nullable=True))
    op.add_column("categories", sa.Column("is_essential", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_categories_accounting_role", "categories", ["accounting_role"])
    op.execute("UPDATE categories SET accounting_role = 'patrimonial' WHERE treat_as_transfer = true")
    op.execute("UPDATE categories SET accounting_role = 'ignored' WHERE is_ignored = true")
    op.execute("UPDATE categories SET accounting_role = 'consumption' WHERE accounting_role IS NULL")

    op.add_column("assets", sa.Column("liquidity_days", sa.Integer(), nullable=True))
    op.add_column("assets", sa.Column("reserve_eligible", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("assets", sa.Column("risk_level", sa.String(length=16), nullable=True))

    op.add_column("payees", sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("payee_tax_ids", sa.Column("fingerprint", sa.String(length=128), nullable=True))
    op.add_column("payee_tax_ids", sa.Column("last4", sa.String(length=4), nullable=True))
    op.add_column("payee_tax_ids", sa.Column("key_version", sa.String(length=32), nullable=True))
    op.alter_column("payee_tax_ids", "value", existing_type=sa.String(length=60), nullable=True)
    op.create_index("ix_payee_tax_ids_fingerprint", "payee_tax_ids", ["workspace_id", "fingerprint"])

    op.add_column("credit_card_bills", sa.Column("status", sa.String(length=12), nullable=False, server_default="open"))
    op.add_column("credit_card_bills", sa.Column("paid_amount", sa.Numeric(15, 2), nullable=False, server_default="0"))
    op.add_column("credit_card_bills", sa.Column("closed_at", sa.Date(), nullable=True))
    op.add_column("credit_card_bills", sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_credit_card_bills_status", "credit_card_bills", ["status"])

    op.create_table(
        "position_valuations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("position_id", sa.UUID(), sa.ForeignKey("positions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("base_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("base_currency", sa.String(length=3), nullable=False, server_default="BRL"),
        sa.Column("fx_rate", sa.Numeric(20, 10), nullable=True),
        sa.Column("valuation_date", sa.Date(), nullable=False),
        sa.Column("basis", sa.String(length=32), nullable=False, server_default="declared"),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("confidence", sa.String(length=20), nullable=False, server_default="low"),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_position_valuations_position_id", "position_valuations", ["position_id"])
    op.create_index("ix_position_valuations_position_date", "position_valuations", ["position_id", "valuation_date"])
    op.create_index("ux_position_valuations_idempotency", "position_valuations", ["position_id", "idempotency_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_position_valuations_idempotency", table_name="position_valuations")
    op.drop_index("ix_position_valuations_position_date", table_name="position_valuations")
    op.drop_index("ix_position_valuations_position_id", table_name="position_valuations")
    op.drop_table("position_valuations")
    op.drop_index("ix_credit_card_bills_status", table_name="credit_card_bills")
    op.drop_column("credit_card_bills", "source_updated_at")
    op.drop_column("credit_card_bills", "closed_at")
    op.drop_column("credit_card_bills", "paid_amount")
    op.drop_column("credit_card_bills", "status")
    op.drop_index("ix_payee_tax_ids_fingerprint", table_name="payee_tax_ids")
    op.alter_column("payee_tax_ids", "value", existing_type=sa.String(length=60), nullable=False)
    op.drop_column("payee_tax_ids", "key_version")
    op.drop_column("payee_tax_ids", "last4")
    op.drop_column("payee_tax_ids", "fingerprint")
    op.drop_column("payees", "is_archived")
    op.drop_column("assets", "risk_level")
    op.drop_column("assets", "reserve_eligible")
    op.drop_column("assets", "liquidity_days")
    op.drop_index("ix_categories_accounting_role", table_name="categories")
    op.drop_column("categories", "is_essential")
    op.drop_column("categories", "accounting_role")
