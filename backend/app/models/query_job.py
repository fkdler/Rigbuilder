from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


class QueryJob(Base):
    """One submitted request and its frozen result.

    ``owner_id`` duplicates the owner of ``conversation_id`` on purpose, and the
    duplication is a correctness requirement rather than a convenience: a job is
    created before the conversation it will end up in, so ``conversation_id`` is
    NULL for the whole queued/running window -- exactly the window in which the
    job status, event stream and cancel endpoint must be authorized. Deriving the
    owner through the conversation would leave those routes with nothing to check
    when they are most reachable (Plan_V4.5 §11.2 lists Job ownership as
    inheriting from the conversation; this column is how that inheritance is
    recorded at creation time instead of resolved later).
    """

    __tablename__ = "query_job"
    __table_args__ = (
        Index("ix_query_job_status", "status"),
        Index("ix_query_job_owner_created", "owner_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, default=uuid4)
    owner_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=True
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversation.id", ondelete="SET NULL"), nullable=True
    )
    requested_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    resolved_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", server_default="queued")
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

