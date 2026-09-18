"""Disposable, globally shared cache for the Zhihu hardware daily surface."""

from datetime import datetime

from sqlalchemy import DateTime, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HardwareDailyCache(Base):
    """One normalized article record, stored in an UNLOGGED PostgreSQL table.

    The ORM uses portable column types so the isolated SQLite test store can
    exercise the service.  The Alembic migration creates the production table as
    PostgreSQL ``UNLOGGED`` because a lost cache is always safe to rebuild.
    """

    __tablename__ = "hardware_daily_cache"

    cache_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_refresh_error: Mapped[str | None] = mapped_column(Text, nullable=True)
