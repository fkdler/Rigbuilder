from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.base import Base


class ConversationContextSnapshot(Base):
    __tablename__ = "conversation_context_snapshot"
    __table_args__ = (
        UniqueConstraint("conversation_id", "version", name="conversation_snapshot_version"),
        CheckConstraint("token_count >= 0", name="token_count_non_negative"),
        Index("ix_conversation_snapshot_conversation_version", "conversation_id", "version"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    covered_through_message_id: Mapped[UUID | None] = mapped_column(ForeignKey("conversation_message.id", ondelete="SET NULL"))
    covered_through_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    conversation: Mapped["Conversation"] = relationship(back_populates="snapshots")
