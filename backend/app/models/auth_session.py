from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


class AuthSession(Base):
    """A device-bound login session (Plan_V4.5 §11.2).

    Only ``token_digest`` is persisted, so the bearer token itself exists only in
    the client and in the response that created it. ``revoked_at`` and
    ``expires_at`` are both checked on every request, which is what makes
    logout, password change and account disable take effect immediately rather
    than at the next token expiry.
    """

    __tablename__ = "auth_session"
    __table_args__ = (
        Index("ix_auth_session_user_id", "user_id"),
        Index("ix_auth_session_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
