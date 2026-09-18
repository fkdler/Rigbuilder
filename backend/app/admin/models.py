"""Response models for the read-only admin API.

Deliberately self-contained in ``app.admin`` rather than added to ``app.schemas``,
so that adding observability touches no existing module.  These models describe
numbers the database already holds; nothing here is written back.

Every optional metric is ``None`` when the underlying key is absent from
``agent_run.metrics`` rather than 0.  Measured: of the 33 keys the loop writes, six
appear in only 107-132 of 183 historical runs, so a missing key and a real zero must
not be reported as the same thing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Percentiles(BaseModel):
    avg: float | None = None
    p50: float | None = None
    p95: float | None = None
    maximum: float | None = None


class AgentStats(BaseModel):
    """Aggregated run statistics for one Agent profile."""

    model_id: str
    runs: int = 0
    completed: int = 0
    failed: int = 0
    completion_rate: float | None = None
    truth_verified: int = 0
    truth_verified_rate: float | None = None
    errors: dict[str, int] = Field(default_factory=dict)

    rounds: Percentiles = Field(default_factory=Percentiles)
    sql_calls: Percentiles = Field(default_factory=Percentiles)
    llm_calls: Percentiles = Field(default_factory=Percentiles)
    tool_calls: Percentiles = Field(default_factory=Percentiles)

    # ---- timing: wall clock and its breakdown -------------------------------
    # The loop's own duration metrics.  Available only on runs that wrote that key
    # (measured: agent_duration_ms on 107 of 183 runs), so every one is optional and
    # metric_coverage below says how many runs carried it.
    #
    # NOTE: no duration is derived from created_at -> updated_at.  agent_run.updated_at
    # uses onupdate=func.now(), and where the three parallel workers commit concurrently
    # one run's updated_at takes the value of another's transaction start, which makes
    # that difference negative.  Measured: 60 of 183 rows have created_at > updated_at.
    # wall_clock_ms comes from the owning job's started_at -> completed_at instead.
    wall_clock_ms: Percentiles = Field(default_factory=Percentiles)
    total_duration_ms: Percentiles = Field(default_factory=Percentiles)
    agent_duration_ms: Percentiles = Field(default_factory=Percentiles)
    llm_duration_ms: Percentiles = Field(default_factory=Percentiles)
    prompt_eval_duration_ms: Percentiles = Field(default_factory=Percentiles)
    generation_duration_ms: Percentiles = Field(default_factory=Percentiles)
    queue_duration_ms: Percentiles = Field(default_factory=Percentiles)
    verification_duration_ms: Percentiles = Field(default_factory=Percentiles)
    fusion_duration_ms: Percentiles = Field(default_factory=Percentiles)

    # Tokens per second over the window.  None when either sum is missing or when the
    # duration key does not cover the whole window, because a rate built from partial
    # coverage looks precise while being wrong.
    tokens_per_second: float | None = None
    tokens_per_second_coverage: float | None = Field(
        default=None,
        description="Fraction of runs in the window that carried llm_duration_ms.",
    )

    first_run_at: datetime | None = None
    last_run_at: datetime | None = None
    observed_span_hours: float | None = Field(
        default=None,
        description="created_at span of the window's runs. A description of when runs "
                    "happened, not of how long any run took.",
    )

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    llm_duration_total_ms: int | None = None
    tool_duration_total_ms: int | None = None

    context_pruned_messages: int | None = None
    duplicate_statements_rejected: int | None = None
    runs_with_usable_candidate_id: int | None = None
    protocol_retries: int | None = None

    # How many runs carried each family of keys, so a None above is never ambiguous.
    metric_coverage: dict[str, int] = Field(default_factory=dict)


class AgentRunRow(BaseModel):
    """One Agent run, with the timing facts an operator needs to read a slow run.

    ``wall_clock_ms`` is the owning job's ``started_at -> completed_at``.  In parallel
    mode the three Agents run concurrently, so measured job wall clock tracks the
    slowest Agent rather than the sum (measured ratio 1.02-1.11 against the max), which
    makes this the honest per-run wall clock even though it is a property of the job.

    ``agent_duration_ms`` and friends are the loop's own numbers and are ``None`` when
    that run never wrote them.  ``created_at``/``updated_at`` are the row's raw stamps,
    reported as-is and never differenced: see the note in ``AgentStats``.
    """

    agent_run_id: str
    model_id: str | None = None
    status: str
    error_code: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    job_id: str | None = None
    request_id: str | None = None
    wall_clock_ms: int | None = None
    total_duration_ms: int | None = None
    agent_duration_ms: int | None = None
    llm_duration_ms: int | None = None
    queue_duration_ms: int | None = None
    verification_duration_ms: int | None = None
    rounds_used: int | None = None
    sql_calls_used: int | None = None
    llm_calls: int | None = None
    tool_calls: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    truth_verified: bool | None = None
    token_floor: int | None = Field(
        default=None,
        description="completion_tokens divided by llm_calls: the generation floor a run "
                    "paid even when it produced nothing usable.",
    )


class DayStats(BaseModel):
    day: datetime
    runs: int = 0
    completed: int = 0
    failed: int = 0
    total_tokens: int | None = None


class AgentStatsResponse(BaseModel):
    window_hours: int
    since: datetime | None = None
    model_filter: str | None = None
    generated_at: datetime
    elapsed_ms: int
    total_runs: int = 0
    agents: list[AgentStats] = Field(default_factory=list)
    by_error_code: dict[str, int] = Field(default_factory=dict)
    by_day: list[DayStats] = Field(default_factory=list)
    recent_runs: list[AgentRunRow] = Field(default_factory=list)
    # Reads agent_run.metrics->>'parallel'; False would mean the serial fallback path ran.
    parallel_runs: int = 0
    serial_runs: int = 0


class JobSummaryRow(BaseModel):
    job_id: str
    status: str
    resolved_mode: str | None = None
    error_code: str | None = None
    duration_ms: int | None = None
    agent_coverage: float | None = None
    event_count: int = 0
    created_at: datetime | None = None


class JobStatsResponse(BaseModel):
    window_hours: int
    since: datetime | None = None
    generated_at: datetime
    elapsed_ms: int
    total_jobs: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    by_resolved_mode: dict[str, int] = Field(default_factory=dict)
    by_error_code: dict[str, int] = Field(default_factory=dict)
    duration_ms: Percentiles = Field(default_factory=Percentiles)
    coverage: Percentiles = Field(default_factory=Percentiles)
    events_by_type: dict[str, int] = Field(default_factory=dict)
    # Distinguishes "not a fusion job" from "fusion that produced no candidate".
    fusion_jobs: int = 0
    fusion_with_zero_coverage: int = 0
    recent: list[JobSummaryRow] = Field(default_factory=list)


class EndpointStatus(BaseModel):
    profile_id: str
    endpoint_url: str
    configured_model_id: str
    reachable: bool
    latency_ms: int | None = None
    error: str | None = None
    loaded_models: list[str] = Field(default_factory=list)
    capability_state: str = "unverified"
    context_size: int | None = None
    context_source: str = 'settings'
    configured_context_size: int | None = None
    max_tool_rounds: int | None = None
    max_sql_calls: int | None = None
    terminal_tool_supported: bool | None = None
    is_local: bool | None = None


class EndpointsResponse(BaseModel):
    generated_at: datetime
    elapsed_ms: int
    profiles: list[EndpointStatus] = Field(default_factory=list)
    distinct_endpoints: bool = False
    distinct_model_ids: bool = False
    configuration_issues: list[str] = Field(default_factory=list)
    parallel_capable: bool = False
    warnings: list[str] = Field(default_factory=list)
    # Settings that decide which loop path actually runs; previously only readable
    # by querying the database or reading .env.
    agent_terminal_tool_supported: bool | None = None
    agent_parallel_required: bool | None = None
    capability_smoke_on_startup: bool | None = None
    agent_max_rounds: int | None = None
    agent_max_sql_calls: int | None = None


class ViewRowCount(BaseModel):
    view: str
    rows: int | None = None
    estimated: bool = False
    error: str | None = None


class TableRowCount(BaseModel):
    table: str
    rows: int | None = None
    estimated: bool = False
    error: str | None = None


class DatabaseOverviewResponse(BaseModel):
    generated_at: datetime
    elapsed_ms: int
    reachable: bool
    release_key: str | None = None
    release_status: str | None = None
    alembic_version: str | None = None
    agent_view_count: int = 0
    truth_table_count: int = 0
    recommendable_entities: int | None = None
    views: list[ViewRowCount] = Field(default_factory=list)
    empty_views: list[str] = Field(default_factory=list)
    key_tables: list[TableRowCount] = Field(default_factory=list)
    empty_key_tables: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AdminSummaryResponse(BaseModel):
    generated_at: datetime
    elapsed_ms: int
    database: DatabaseOverviewResponse
    endpoints: EndpointsResponse
    agents: AgentStatsResponse
    jobs: JobStatsResponse


__all__ = [
    "AdminSummaryResponse",
    "AgentRunRow",
    "AgentStats",
    "AgentStatsResponse",
    "DatabaseOverviewResponse",
    "DayStats",
    "EndpointStatus",
    "EndpointsResponse",
    "JobStatsResponse",
    "JobSummaryRow",
    "Percentiles",
    "TableRowCount",
    "ViewRowCount",
]
