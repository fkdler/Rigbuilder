"""Conversation list/detail/delete API contracts (Plan V3.4).

The list endpoint returns server-truth summaries so the sidebar no longer needs
localStorage as the source of sessions.  The detail endpoint restores messages,
jobs (status + final result + natural language) and agent summaries so a user
can switch conversations and recover the full timeline without page reload.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ConversationMessageInfo(BaseModel):
    sequence_no: int
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: datetime


class ConversationJobInfo(BaseModel):
    job_id: UUID
    status: str
    request_message: str
    resolved_mode: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    result_natural_language: Any | None = None
    agent_coverage: float | None = None
    agent_summary: list[dict[str, Any]] = Field(default_factory=list)
    trace_summary: dict[str, Any] | None = None


class ConversationSummary(BaseModel):
    id: UUID
    status: str
    title: str
    last_message_summary: str
    updated_at: datetime
    running_job: bool = False
    latest_job_status: str | None = None
    active_job_id: UUID | None = None


class ConversationDetail(BaseModel):
    id: UUID
    status: str
    messages: list[ConversationMessageInfo] = Field(default_factory=list)
    jobs: list[ConversationJobInfo] = Field(default_factory=list)


__all__ = [
    "ConversationDetail",
    "ConversationJobInfo",
    "ConversationMessageInfo",
    "ConversationSummary",
]
