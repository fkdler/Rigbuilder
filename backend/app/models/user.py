from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base

USER_ROLES = ("user", "admin")
USER_STATUSES = ("active", "disabled")


class User(Base):
    """A local account (Plan_V4.5 §11.2).

    Uniqueness of ``username`` is case-insensitive and is enforced by the
    functional index ``uq_app_user_username_lower`` created in migration
    ``0010_user_accounts`` rather than by a plain unique constraint: accounts are
    human-addressed, so ``Alex`` and ``alex`` must not be two identities. Reads
    therefore always compare with ``func.lower(User.username)``.

    ``password_hash`` holds the self-describing PBKDF2 string produced by
    ``app.core.security.hash_password``. No plaintext password, no reversible
    encoding and no password *hint* is stored anywhere.
    """

    __tablename__ = "app_user"
    __table_args__ = (Index("ix_app_user_status", "status"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user", server_default="user")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
