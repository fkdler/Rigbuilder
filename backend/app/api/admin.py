"""Admin API: operational statistics plus the account/catalogue workspace.

The original statistics endpoints here only read; admin_workspace adds guarded
account deletion and audited catalogue edits. Nothing here is imported by the Agent loop,
the fusion service, the verifier or the tool layer, so an operator console can never
change the Agent execution protocol.

The routes live under ``/api/admin`` and do not overlap any existing path.  Existing
response shapes are untouched: this module adds surface, it does not change it.

Access (Plan_V4.5 §11.1 / §11.3 U4): the whole surface used to be anonymous, which
meant job statistics and database internals were readable by anyone who could reach
the port.  Now every route requires an authenticated account, and the four
aggregate/overview routes additionally require the ``admin`` role, because they
expose operational detail -- SQL and HTTP failure text, table row counts, release
keys -- that is not user data and not the user's business.

``/endpoints`` is the one exception, held at "any logged-in account": the chat
header uses it to say up front that a llama-server is down, instead of letting the
request fail as ``fusion_failed`` after ~115 s.  It reports reachability, the loaded
model id and a latency, and is about availability rather than about anyone's data.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query

from app.admin.introspection import collect_database_overview, collect_endpoint_status
from app.admin.models import (
    AdminSummaryResponse,
    AgentStatsResponse,
    DatabaseOverviewResponse,
    EndpointsResponse,
    JobStatsResponse,
)
from app.admin.stats import collect_agent_stats, collect_job_stats
from app.api.deps import current_user, require_admin
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(current_user)])

# Applied per route rather than on the router: ``/endpoints`` is deliberately open
# to every logged-in account (see the module docstring).
_ADMIN_ONLY = [Depends(require_admin)]

# 0 means "all time"; the cap keeps an operator from accidentally scanning forever.
_WINDOW_DESCRIPTION = (
    "Reporting window in hours. 0 (default) means all recorded history; "
    "the value is only ever used to bound a read."
)


def _log_failure(surface: str, exc: Exception) -> None:
    logger.warning("admin %s failed: %s: %s", surface, type(exc).__name__, exc)


@router.get(
    "/stats/agents",
    response_model=AgentStatsResponse,
    summary="Per-Agent run statistics",
    dependencies=_ADMIN_ONLY,
)
def agent_stats(
    window_hours: int = Query(default=0, ge=0, le=24 * 365, description=_WINDOW_DESCRIPTION),
    model_id: str | None = Query(default=None, max_length=255, description="Restrict to one Agent."),
    limit: int = Query(default=20, ge=1, le=100),
) -> AgentStatsResponse:
    """Run counts, success rates, token use and the full timing breakdown per Agent."""
    with SessionLocal() as session:
        return collect_agent_stats(
            session, window_hours=window_hours, model_id=model_id, limit=limit,
        )


@router.get(
    "/stats/jobs",
    response_model=JobStatsResponse,
    summary="Query job and event statistics",
    dependencies=_ADMIN_ONLY,
)
def job_stats(
    window_hours: int = Query(default=0, ge=0, le=24 * 365, description=_WINDOW_DESCRIPTION),
    limit: int = Query(default=10, ge=1, le=100, description="How many recent jobs to list."),
) -> JobStatsResponse:
    """Job outcomes, wall-clock percentiles, coverage and the recent job list."""
    with SessionLocal() as session:
        return collect_job_stats(session, window_hours=window_hours, limit=limit)


@router.get(
    "/database",
    response_model=DatabaseOverviewResponse,
    summary="Database overview",
    dependencies=_ADMIN_ONLY,
)
def database_overview(
    max_views: int = Query(default=32, ge=1, le=200),
    max_tables: int = Query(default=40, ge=1, le=200),
) -> DatabaseOverviewResponse:
    """Release, schema size, per-view row counts and which tables are still empty."""
    with SessionLocal() as session:
        return collect_database_overview(session, max_views=max_views, max_tables=max_tables)


@router.get(
    "/endpoints",
    response_model=EndpointsResponse,
    summary="Live inference endpoint state",
)
async def endpoint_status(
    timeout_seconds: float = Query(default=5.0, ge=0.5, le=30.0,
                                   description="Per-endpoint probe timeout."),
    overall_timeout_seconds: float = Query(default=12.0, ge=1.0, le=60.0,
                                           description="Cap on the whole probe."),
) -> EndpointsResponse:
    """Reachability, loaded model and capability state of each configured endpoint."""
    return await collect_endpoint_status(
        timeout_seconds=timeout_seconds, overall_timeout_seconds=overall_timeout_seconds,
    )


@router.get(
    "/summary",
    response_model=AdminSummaryResponse,
    summary="Everything the admin console shows, in one call",
    dependencies=_ADMIN_ONLY,
)
async def summary(
    window_hours: int = Query(default=0, ge=0, le=24 * 365, description=_WINDOW_DESCRIPTION),
    limit: int = Query(default=10, ge=1, le=50),
) -> AdminSummaryResponse:
    """One request for the whole console, so a page does not fan out to five calls.

    Each section degrades on its own: a database that is down, or an endpoint that hangs,
    still leaves the other sections populated rather than failing the whole response.
    """
    started = time.perf_counter()

    # Probe endpoints first: it opens no database session, so a stalled database below
    # cannot delay the part of the page that would still have been useful.
    endpoints = await collect_endpoint_status()

    with SessionLocal() as session:
        try:
            database = collect_database_overview(session)
        except Exception as exc:  # noqa: BLE001 - one section must not sink the console
            _log_failure("database_overview", exc)
            database = DatabaseOverviewResponse(
                generated_at=datetime.now(timezone.utc), elapsed_ms=0, reachable=False,
                warnings=[f"section_failed: {type(exc).__name__}"],
            )
        try:
            agents = collect_agent_stats(session, window_hours=window_hours, limit=limit)
        except Exception as exc:  # noqa: BLE001
            _log_failure("agent_stats", exc)
            agents = AgentStatsResponse(
                window_hours=window_hours, generated_at=datetime.now(timezone.utc),
                elapsed_ms=0, total_runs=0,
            )
        try:
            jobs = collect_job_stats(session, window_hours=window_hours, limit=limit)
        except Exception as exc:  # noqa: BLE001
            _log_failure("job_stats", exc)
            jobs = JobStatsResponse(
                window_hours=window_hours, generated_at=datetime.now(timezone.utc),
                elapsed_ms=0, total_jobs=0,
            )

    return AdminSummaryResponse(
        generated_at=datetime.now(timezone.utc),
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        database=database,
        endpoints=endpoints,
        agents=agents,
        jobs=jobs,
    )


from app.api.admin_workspace import router as workspace_router

router.include_router(workspace_router)
from app.admin.catalog_editor import router as catalog_editor_router

router.include_router(catalog_editor_router)

__all__ = ["router"]
