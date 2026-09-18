from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.base import Base


class Conversation(Base):
    """The shared user-visible conversation boundary across all future agents.

    ``owner_id`` is the server-side identity boundary (Plan_V4.5 §11.2). It is
    deliberately nullable in the schema and is never written from a client-supplied
    value: history created before accounts existed stays unowned and therefore
    invisible to every account, rather than being guessed onto the first user who
    registers. ``(owner_id, updated_at)`` is indexed because the sidebar always
    reads "this user's conversations, newest first".
    """

    __tablename__ = "conversation"
    __table_args__ = (Index("ix_conversation_owner_updated", "owner_id", "updated_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="ConversationMessage.sequence_no"
    )
    snapshots: Mapped[list["ConversationContextSnapshot"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="ConversationContextSnapshot.version"
    )
    constraints: Mapped[list["UserConstraint"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")
