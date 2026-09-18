"""Operator audit and site usage survive account/conversation deletion."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


class TokenUsage(Base):
    __tablename__ = "token_usage"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(BigInteger)
    completion_tokens: Mapped[int | None] = mapped_column(BigInteger)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="provider")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class AdminChange(Base):
    __tablename__ = "admin_change"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    target_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    before: Mapped[dict] = mapped_column(JSON, nullable=False)
    after: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
