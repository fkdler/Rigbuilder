from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


class ToolCall(Base):
    """One validated database-tool invocation, persisted for SQL correctness evals."""

    __tablename__ = "tool_call"
    __table_args__ = (Index("ix_tool_call_run_sequence", "agent_run_id", "sequence_no"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    agent_run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_run.id", ondelete="CASCADE"), nullable=False)
    agent_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_message.id", ondelete="SET NULL"), nullable=True
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    tool: Mapped[str] = mapped_column(String(50), nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    query_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    result_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
