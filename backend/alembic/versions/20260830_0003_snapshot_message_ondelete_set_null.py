"""allow snapshot source messages to be removed safely

Revision ID: 20260830_0003
Revises: 20260830_0002
Create Date: 2026-08-30 00:01:00
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260830_0003"
down_revision: Union[str, Sequence[str], None] = "20260830_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("fk_snapshot_covered_message", "conversation_context_snapshot", type_="foreignkey")
    op.create_foreign_key(
        "fk_snapshot_covered_message",
        "conversation_context_snapshot",
        "conversation_message",
        ["covered_through_message_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_snapshot_covered_message", "conversation_context_snapshot", type_="foreignkey")
    op.create_foreign_key(
        "fk_snapshot_covered_message",
        "conversation_context_snapshot",
        "conversation_message",
        ["covered_through_message_id"],
        ["id"],
        ondelete="RESTRICT",
    )
