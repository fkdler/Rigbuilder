"""create agent run/message/tool_call log tables

Revision ID: 20260831_0004
Revises: 20260830_0003
Create Date: 2026-08-31 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260831_0004"
down_revision: Union[str, Sequence[str], None] = "20260830_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("model_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"], name="fk_agent_run_conversation_id_conversation", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_agent_run"),
    )
    op.create_index("ix_agent_run_request_id", "agent_run", ["request_id"], unique=False)

    op.create_table(
        "agent_message",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("role IN ('system', 'user', 'assistant', 'tool')", name="role_valid"),
        sa.CheckConstraint("token_count >= 0", name="token_count_non_negative"),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_run.id"], name="fk_agent_message_agent_run_id_agent_run", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_agent_message"),
        sa.UniqueConstraint("agent_run_id", "sequence_no", name="agent_message_run_sequence"),
    )
    op.create_index("ix_agent_message_run_sequence", "agent_message", ["agent_run_id", "sequence_no"], unique=False)

    op.create_table(
        "tool_call",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column("agent_message_id", sa.Uuid(), nullable=True),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("tool", sa.String(length=50), nullable=False),
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("query_id", sa.String(length=36), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("result_truncated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["agent_message_id"], ["agent_message.id"], name="fk_tool_call_agent_message_id_agent_message", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_run.id"], name="fk_tool_call_agent_run_id_agent_run", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_tool_call"),
    )
    op.create_index("ix_tool_call_run_sequence", "tool_call", ["agent_run_id", "sequence_no"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tool_call_run_sequence", table_name="tool_call")
    op.drop_table("tool_call")
    op.drop_index("ix_agent_message_run_sequence", table_name="agent_message")
    op.drop_table("agent_message")
    op.drop_index("ix_agent_run_request_id", table_name="agent_run")
    op.drop_table("agent_run")
