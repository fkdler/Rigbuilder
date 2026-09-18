from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.fusion import ConfirmedConstraint

QueryMode = Literal["auto", "chat", "fusion"]
ResolvedMode = Literal["chat", "fusion"]
JobStatus = Literal["queued", "running", "completed", "failed", "cancel_requested", "cancelled"]


class QueryJobRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4_000)
    conversation_id: UUID | None = None
    constraints: list[ConfirmedConstraint] = Field(default_factory=list)
    top_k: int = Field(default=3, ge=1, le=10)
    mode: QueryMode = "auto"

    @model_validator(mode="after")
    def unique_constraints(self) -> "QueryJobRequest":
        ids = [item.constraint_id for item in self.constraints]
        if len(ids) != len(set(ids)):
            raise ValueError("constraint_id values must be unique")
        return self


class QueryEventResponse(BaseModel):
    sequence: int
    event_type: str
    phase: str
    status: str
    agent: str | None = None
    model: str | None = None
    title: str
    summary: str | None = None
    detail: dict[str, Any] | None = None
    duration_ms: float | None = None
    created_at: datetime


class QueryJobResponse(BaseModel):
    id: UUID
    request_id: UUID
    conversation_id: UUID | None
    requested_mode: QueryMode
    resolved_mode: ResolvedMode | None
    status: JobStatus
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error: str | None = None
    last_sequence: int = 0
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ToolTraceResponse(BaseModel):
    sequence: int
    tool: str
    arguments: dict[str, Any]
    query_id: str | None = None
    success: bool
    error_code: str | None = None
    truncated: bool = False
    duration_ms: int | None = None
    result_summary: str | None = None


class AgentTraceResponse(BaseModel):
    agent_run_id: UUID
    model_id: str | None = None
    status: str
    error_code: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[UUID] = Field(default_factory=list)
    tools: list[ToolTraceResponse] = Field(default_factory=list)


class QueryTraceResponse(BaseModel):
    job_id: UUID
    request_id: UUID
    agents: list[AgentTraceResponse] = Field(default_factory=list)


__all__ = [
    "AgentTraceResponse", "JobStatus", "QueryEventResponse", "QueryJobRequest",
    "QueryJobResponse", "QueryMode", "QueryTraceResponse", "ResolvedMode",
    "ToolTraceResponse",
]
