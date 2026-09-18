from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.base import Base


class UserConstraint(Base):
    """Reserved structured, user-confirmed constraints for later extraction."""

    __tablename__ = "user_constraint"
    __table_args__ = (UniqueConstraint("conversation_id", "constraint_key", name="conversation_constraint_key"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False)
    constraint_key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[dict[str, Any] | list[Any] | str | int | float | bool] = mapped_column(JSONB, nullable=False)
    source_message_id: Mapped[UUID | None] = mapped_column(ForeignKey("conversation_message.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="confirmed", server_default="confirmed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    conversation: Mapped["Conversation"] = relationship(back_populates="constraints")
