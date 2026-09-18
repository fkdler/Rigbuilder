"""Wire contracts for the local account system (Plan_V4.5 §11)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Deliberately permissive: the brief is "username + password, no restrictions
    # yet", so only the two rules that keep the value storable and printable are
    # enforced (non-empty, fits the column). Strength policy belongs to a later
    # batch and to a settings switch, not to a field validator that would silently
    # start rejecting accounts the day someone tightens it.
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserResponse(BaseModel):
    id: UUID
    username: str
    role: str
    created_at: datetime
    last_login_at: datetime | None = None


class SessionResponse(BaseModel):
    """The one and only time the bearer token is ever returned."""

    token: str
    expires_at: datetime
    user: UserResponse


class UpdateUsernameRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


__all__ = [
    "ChangePasswordRequest",
    "LoginRequest",
    "RegisterRequest",
    "SessionResponse",
    "UpdateUsernameRequest",
    "UserResponse",
]
