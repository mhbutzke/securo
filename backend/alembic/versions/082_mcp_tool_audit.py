"""Add metadata-only MCP tool audit trail.

Revision ID: 082
Revises: 081
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "082"
down_revision: Union[str, None] = "081"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcp_tool_audit",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("jti", sa.String(length=64), nullable=True),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("access", sa.String(length=8), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("result_status", sa.String(length=16), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_mcp_tool_audit_workspace_created", "mcp_tool_audit", ["workspace_id", "created_at"])
    op.create_index("ix_mcp_tool_audit_jti", "mcp_tool_audit", ["jti"])


def downgrade() -> None:
    op.drop_index("ix_mcp_tool_audit_jti", table_name="mcp_tool_audit")
    op.drop_index("ix_mcp_tool_audit_workspace_created", table_name="mcp_tool_audit")
    op.drop_table("mcp_tool_audit")
