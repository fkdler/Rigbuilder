"""Independent token ledger and administrator change log.

Historical llm_completed events cover only previously recorded calls; they are
not claimed to be a complete reconstruction of past website usage.
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_admin_workspace"
down_revision = "0010_user_accounts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "token_usage",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("prompt_tokens", sa.BigInteger()),
        sa.Column("completion_tokens", sa.BigInteger()),
        sa.Column("total_tokens", sa.BigInteger()),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_token_usage_created_at", "token_usage", ["created_at"])
    op.create_table(
        "admin_change",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    # One final event per call. Never add AgentRun totals (same calls), live
    # counters (cumulative) or job totals (same calls again).
    op.execute("""
        INSERT INTO token_usage (id, model, prompt_tokens, completion_tokens, total_tokens, source, created_at)
        SELECT id, COALESCE(model, 'unknown'), p, c, COALESCE(t, p + c), 'historical_event', created_at
        FROM (
            SELECT id, model, created_at,
              CASE WHEN detail->>'prompt_tokens' ~ '^[0-9]{1,18}$' THEN (detail->>'prompt_tokens')::bigint END p,
              CASE WHEN detail->>'completion_tokens' ~ '^[0-9]{1,18}$' THEN (detail->>'completion_tokens')::bigint END c,
              CASE WHEN detail->>'total_tokens' ~ '^[0-9]{1,18}$' THEN (detail->>'total_tokens')::bigint END t
            FROM query_event WHERE event_type = 'llm_completed'
        ) recorded
    """)


def downgrade():
    op.drop_table("admin_change")
    op.drop_index("ix_token_usage_created_at", table_name="token_usage")
    op.drop_table("token_usage")
