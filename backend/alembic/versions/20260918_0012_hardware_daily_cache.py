"""Disposable global cache for the Zhihu hardware daily.

The table intentionally has no relationship to conversations, accounts, Agents or
the Truth DB.  PostgreSQL may clear an UNLOGGED table after crash recovery; the
reader service treats that exactly like a cold cache and refills it on demand.
"""

from alembic import op


revision = "0012_hardware_daily_cache"
down_revision = "0011_admin_workspace"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE UNLOGGED TABLE hardware_daily_cache (
            cache_key VARCHAR(128) PRIMARY KEY,
            payload JSONB NOT NULL,
            fetched_at TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            last_refresh_error TEXT NULL
        )
    """)


def downgrade():
    op.drop_table("hardware_daily_cache")
