"""local accounts, login sessions and per-user ownership

Revision ID: 0010_user_accounts
Revises: 0009_performance_surface
Create Date: 2026-09-16

Plan_V4.5 §11 states plainly that the system has no accounts: ``user_constraint``
is a table of confirmed build constraints and not a user table, ``conversation``
has no owner, and every route is open. This revision is the U1 step -- the
schema only, additive and reversible -- for the user system described in §11.2.

What it adds:

``app_user``
    The identity record. ``password_hash`` only ever holds the self-describing
    PBKDF2 string from ``app.core.security``; there is no column a plaintext or
    reversibly-encoded password could live in. Identity is a *name*, not an
    e-mail, because a local single-node deployment has no mail loop to verify one.

``auth_session``
    One row per logged-in device. Only the SHA-256 digest of the bearer token is
    stored, together with ``expires_at`` and ``revoked_at`` -- which is what makes
    logout, password change and disable take effect on the next request instead of
    whenever the token would have expired.

``conversation.owner_id`` / ``query_job.owner_id``
    Both are added **nullable and unbackfilled**. Existing rows keep NULL and
    therefore belong to nobody: §11.2 forbids handing all shared history to
    whichever account registers first, and three plausible policies exist
    (explicit claim, offline admin assignment, permanent non-public). NULL is the
    only value that does not silently pick one. Authorization treats NULL as "not
    owned by any account", so legacy conversations are invisible rather than
    shared.

    ``query_job`` carries its own ``owner_id`` rather than deriving it through
    ``conversation_id``: that column is NULL until the fusion run creates the
    conversation, so for the whole queued/running window the job has no
    conversation to inherit from -- and that window is precisely when the status,
    event-stream and cancel routes are reachable.

Both ownership foreign keys are ``ON DELETE CASCADE``: deleting an account
removes its conversations and jobs. That is the retention policy §11.2 asks for,
and it is recorded here rather than left to the application, so a manual
``DELETE FROM app_user`` cannot leave orphans behind. No endpoint deletes a user
in this revision.

Scope discipline: this revision touches only ``public``. It does not read, write
or migrate the ``truth`` schema or any release package, so it stays in its own
batch as §11.3 requires.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_user_accounts"
down_revision: Union[str, Sequence[str], None] = "0009_performance_surface"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), server_default="user", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('user', 'admin')", name="ck_app_user_role_valid"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_app_user_status_valid"),
        sa.PrimaryKeyConstraint("id", name="pk_app_user"),
    )
    op.create_index("ix_app_user_status", "app_user", ["status"])
    # Accounts are addressed by a human-typed name, so `Alex` and `alex` must not
    # become two identities. A functional unique index enforces that in one place
    # instead of relying on every caller to normalise before comparing.
    op.create_index(
        "uq_app_user_username_lower", "app_user", [sa.text("lower(username)")], unique=True,
    )

    op.create_table(
        "auth_session",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name="fk_auth_session_user_id_app_user", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auth_session"),
    )
    op.create_index("uq_auth_session_token_digest", "auth_session", ["token_digest"], unique=True)
    op.create_index("ix_auth_session_user_id", "auth_session", ["user_id"])
    op.create_index("ix_auth_session_expires_at", "auth_session", ["expires_at"])

    op.add_column("conversation", sa.Column("owner_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_conversation_owner_id_app_user", "conversation", "app_user",
        ["owner_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index("ix_conversation_owner_updated", "conversation", ["owner_id", "updated_at"])

    op.add_column("query_job", sa.Column("owner_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_query_job_owner_id_app_user", "query_job", "app_user",
        ["owner_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index("ix_query_job_owner_created", "query_job", ["owner_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_query_job_owner_created", table_name="query_job")
    op.drop_constraint("fk_query_job_owner_id_app_user", "query_job", type_="foreignkey")
    op.drop_column("query_job", "owner_id")

    op.drop_index("ix_conversation_owner_updated", table_name="conversation")
    op.drop_constraint("fk_conversation_owner_id_app_user", "conversation", type_="foreignkey")
    op.drop_column("conversation", "owner_id")

    op.drop_index("ix_auth_session_expires_at", table_name="auth_session")
    op.drop_index("ix_auth_session_user_id", table_name="auth_session")
    op.drop_index("uq_auth_session_token_digest", table_name="auth_session")
    op.drop_table("auth_session")

    op.drop_index("uq_app_user_username_lower", table_name="app_user")
    op.drop_index("ix_app_user_status", table_name="app_user")
    op.drop_table("app_user")
