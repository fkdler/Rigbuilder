"""create persistent query jobs and execution events

Revision ID: 0007_create_query_jobs
Revises: 0006_freeze_truth_v3_1
Create Date: 2026-09-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_create_query_jobs"
down_revision = "0006_freeze_truth_v3_1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "query_job",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("requested_mode", sa.String(16), nullable=False),
        sa.Column("resolved_mode", sa.String(16), nullable=True),
        sa.Column("status", sa.String(24), server_default="queued", nullable=False),
        sa.Column("request_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_query_job_status", "query_job", ["status"])
    op.create_table(
        "query_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("phase", sa.String(40), nullable=False),
        sa.Column("status", sa.String(24), server_default="completed", nullable=False),
        sa.Column("agent", sa.String(255), nullable=True),
        sa.Column("model", sa.String(255), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["query_job.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "sequence", name="query_event_job_sequence"),
    )
    op.create_index("ix_query_event_job_sequence", "query_event", ["job_id", "sequence"])


def downgrade() -> None:
    op.drop_index("ix_query_event_job_sequence", table_name="query_event")
    op.drop_table("query_event")
    op.drop_index("ix_query_job_status", table_name="query_job")
    op.drop_table("query_job")
