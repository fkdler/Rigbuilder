"""create conversation context tables

Revision ID: 20260830_0002
Revises: 20260827_0001
Create Date: 2026-08-30 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260830_0002"
down_revision: Union[str, Sequence[str], None] = "20260827_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_conversation"),
    )
    op.create_table(
        "conversation_message",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("role IN ('user', 'assistant', 'system')", name="role_valid"),
        sa.CheckConstraint("token_count >= 0", name="token_count_non_negative"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"], name="fk_conversation_message_conversation_id_conversation", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_conversation_message"),
        sa.UniqueConstraint("conversation_id", "sequence_no", name="conversation_sequence"),
    )
    op.create_index("ix_conversation_message_conversation_sequence", "conversation_message", ["conversation_id", "sequence_no"], unique=False)
    op.create_table(
        "conversation_context_snapshot",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("covered_through_message_id", sa.Uuid(), nullable=True),
        sa.Column("covered_through_sequence", sa.Integer(), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("token_count >= 0", name="token_count_non_negative"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"], name="fk_conversation_context_snapshot_conversation_id_conversation", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["covered_through_message_id"], ["conversation_message.id"], name="fk_snapshot_covered_message", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_conversation_context_snapshot"),
        sa.UniqueConstraint("conversation_id", "version", name="conversation_snapshot_version"),
    )
    op.create_index("ix_conversation_snapshot_conversation_version", "conversation_context_snapshot", ["conversation_id", "version"], unique=False)
    op.create_table(
        "user_constraint",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("constraint_key", sa.String(length=100), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_message_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="confirmed", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"], name="fk_user_constraint_conversation_id_conversation", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_message_id"], ["conversation_message.id"], name="fk_user_constraint_source_message_id_conversation_message", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_user_constraint"),
        sa.UniqueConstraint("conversation_id", "constraint_key", name="conversation_constraint_key"),
    )


def downgrade() -> None:
    op.drop_table("user_constraint")
    op.drop_index("ix_conversation_snapshot_conversation_version", table_name="conversation_context_snapshot")
    op.drop_table("conversation_context_snapshot")
    op.drop_index("ix_conversation_message_conversation_sequence", table_name="conversation_message")
    op.drop_table("conversation_message")
    op.drop_table("conversation")
