from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.base import Base


class ConversationMessage(Base):
    __tablename__ = "conversation_message"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence_no", name="conversation_sequence"),
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="role_valid"),
        CheckConstraint("token_count >= 0", name="token_count_non_negative"),
        Index("ix_conversation_message_conversation_sequence", "conversation_id", "sequence_no"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
