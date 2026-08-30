"""add MCP token revocation audit table

Revision ID: 078
Revises: 077
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "078"
down_revision: Union[str, None] = "077"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcp_token_revocations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_mcp_token_revocations_jti", "mcp_token_revocations", ["jti"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_mcp_token_revocations_jti", table_name="mcp_token_revocations")
    op.drop_table("mcp_token_revocations")
