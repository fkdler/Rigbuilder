"""Read-only SQL aggregation over the records the Agent loop already persists.

Every number here comes from ``agent_run.metrics`` (JSONB), ``query_job`` or
``query_event``.  Nothing is written, nothing is recomputed that the writer already
decided, and all grouping happens in SQL so a large corpus does not get pulled into
Python.

Two measurement-driven decisions shape this module:

* Of the 33 keys ``agent_run.metrics`` carries, six appear in only 107-132 of the 183
  historical runs.  A missing key therefore reports ``None``, never 0, and
  ``metric_coverage`` states how many runs carried it.
* ``agent_coverage`` is read straight from the stored payload rather than recomputed,
  so the admin view can never disagree with the frontend.  It is ``None`` for a
  non-fusion job and ``0.0`` for a fusion that produced no verified candidate; those
  two facts are reported separately.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.admin.models import (
    AgentRunRow,
    AgentStats,
    AgentStatsResponse,
    DayStats,
    JobStatsResponse,
    JobSummaryRow,
    Percentiles,
)

# Metrics summed across the window.  Each is a JSONB key written by the loop.
_SUM_KEYS: dict[str, str] = {
    "prompt_tokens": "prompt_tokens",
    "completion_tokens": "completion_tokens",
    "total_tokens": "total_tokens",
    "llm_duration_ms": "llm_duration_ms",
    "tool_duration_ms": "tool_duration_ms",
    "prompt_eval_duration_ms": "prompt_eval_duration_ms",
    "generation_duration_ms": "generation_duration_ms",
    "context_pruned_messages": "context_pruned_messages",
    "duplicate_statements_rejected": "duplicate_statements_rejected",
    "protocol_retries": "protocol_retries",
}

# Metrics whose percentile spread is useful.  These are the knob-like values the loop
# reads from Settings, so their distribution shows how close runs come to the limits;
# the duration keys show where the wall clock actually goes.
_PERCENTILE_KEYS: dict[str, str] = {
    "rounds": "rounds_used",
    "sql_calls": "sql_calls_used",
    "llm_calls": "llm_calls",
    "tool_calls": "tool_calls",
    "total_duration_ms": "total_duration_ms",
    "agent_duration_ms": "agent_duration_ms",
    "llm_duration_ms": "llm_duration_ms",
    "prompt_eval_duration_ms": "prompt_eval_duration_ms",
    "generation_duration_ms": "generation_duration_ms",
    "queue_duration_ms": "queue_duration_ms",
    "verification_duration_ms": "verification_duration_ms",
    "fusion_duration_ms": "fusion_duration_ms",
}

_COVERAGE_KEYS: tuple[str, ...] = (
    "rounds_used", "sql_calls_used", "llm_calls", "tool_calls",
    "total_tokens", "llm_duration_ms", "total_duration_ms", "agent_duration_ms",
    "queue_duration_ms", "verification_duration_ms",
    "context_pruned_messages", "duplicate_statements_rejected",
    "known_candidate_ids",
)


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _rate(numerator: int, denominator: int) -> float | None:
    if not denominator:
        return None
    return round(numerator / denominator, 6)


def _window_start(window_hours: int) -> datetime | None:
    """Start of the reporting window, or None for "all time".

    Computed in Python rather than with a SQL interval literal so the statement stays
    fully parameterised; a negative window means all time.
    """
    if window_hours <= 0:
        return None
    return datetime.now(timezone.utc) - timedelta(hours=window_hours)


def _where(since: datetime | None, model_id: str | None) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if since is not None:
        clauses.append("created_at >= :since")
        params["since"] = since
    if model_id:
        clauses.append("model_id = :model_id")
        params["model_id"] = model_id
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _percentiles(session: Session, key: str, since: datetime | None,
                 model_id: str | None) -> tuple[dict[str, Percentiles], int]:
    clauses = [f"metrics->>'{key}' IS NOT NULL"]
    params: dict[str, Any] = {}
    if since is not None:
        clauses.append("created_at >= :since")
        params["since"] = since
    if model_id:
        clauses.append("model_id = :model_id")
        params["model_id"] = model_id
    where = " WHERE " + " AND ".join(clauses)

    statement = text(f"""
        SELECT model_id,
               avg((metrics->>'{key}')::numeric)                                          AS avg,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY (metrics->>'{key}')::numeric)  AS p50,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY (metrics->>'{key}')::numeric) AS p95,
               max((metrics->>'{key}')::numeric)                                          AS maximum,
               count(*)                                                                   AS n
        FROM agent_run{where}
        GROUP BY model_id
    """)
    rows = session.execute(statement, params).all()
    result: dict[str, Percentiles] = {}
    total = 0
    for model, avg, p50, p95, maximum, n in rows:
        result[str(model)] = Percentiles(
            avg=_float_or_none(avg), p50=_float_or_none(p50),
            p95=_float_or_none(p95), maximum=_float_or_none(maximum),
        )
        total += int(n or 0)
    return result, total


def _job_wall_clock_percentiles(session: Session, since: datetime | None,
                                model_id: str | None) -> dict[str, Percentiles]:
    """Per-Agent wall clock taken from the owning job, not from the run's own stamps.

    ``agent_run.updated_at`` uses ``onupdate=func.now()``; where the three parallel
    workers commit concurrently one run picks up another's transaction start, so
    ``created_at -> updated_at`` goes negative.  Measured: 60 of 183 rows have
    ``created_at > updated_at``, and the derived difference reached -221 s.  The job's
    ``started_at -> completed_at`` is a single consistent interval per request, and in
    parallel mode it tracks the slowest Agent (measured ratio 1.02-1.11 against the max),
    so it is the honest per-run wall clock.
    """
    clauses = ["j.started_at IS NOT NULL", "j.completed_at IS NOT NULL"]
    params: dict[str, Any] = {}
    if since is not None:
        clauses.append("j.created_at >= :since")
        params["since"] = since
    if model_id:
        clauses.append("a.model_id = :model_id")
        params["model_id"] = model_id
    where = " WHERE " + " AND ".join(clauses)

    rows = session.execute(text(f"""
        SELECT a.model_id,
               avg(extract(epoch from (j.completed_at - j.started_at)) * 1000)                    AS avg,
               percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY extract(epoch from (j.completed_at - j.started_at)) * 1000)           AS p50,
               percentile_cont(0.95) WITHIN GROUP (
                   ORDER BY extract(epoch from (j.completed_at - j.started_at)) * 1000)           AS p95,
               max(extract(epoch from (j.completed_at - j.started_at)) * 1000)                    AS maximum
        FROM agent_run a JOIN query_job j ON j.request_id = a.request_id{where}
        GROUP BY a.model_id
    """), params).all()
    return {
        str(row[0]): Percentiles(
            avg=_float_or_none(row[1]), p50=_float_or_none(row[2]),
            p95=_float_or_none(row[3]), maximum=_float_or_none(row[4]),
        )
        for row in rows
    }


def _recent_run_rows(session: Session, since: datetime | None,
                     model_id: str | None, limit: int) -> list[AgentRunRow]:
    # Built directly rather than reusing _where(), because every clause needs the "a."
    # alias once agent_run is joined to query_job.
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if since is not None:
        clauses.append("a.created_at >= :since")
        params["since"] = since
    if model_id:
        clauses.append("a.model_id = :model_id")
        params["model_id"] = model_id
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = session.execute(text(f"""
        SELECT a.id, a.model_id, a.status, a.error_code, a.created_at, a.updated_at,
               a.request_id, j.id                                                            AS job_id,
               extract(epoch from (j.completed_at - j.started_at)) * 1000                    AS wall_clock_ms,
               (a.metrics->>'total_duration_ms')::numeric                                    AS total_ms,
               (a.metrics->>'agent_duration_ms')::numeric                                    AS agent_ms,
               (a.metrics->>'llm_duration_ms')::numeric                                      AS llm_ms,
               (a.metrics->>'queue_duration_ms')::numeric                                    AS queue_ms,
               (a.metrics->>'verification_duration_ms')::numeric                             AS verify_ms,
               (a.metrics->>'rounds_used')::int                                              AS rounds,
               (a.metrics->>'sql_calls_used')::int                                           AS sql_calls,
               (a.metrics->>'llm_calls')::int                                                AS llm_calls,
               (a.metrics->>'tool_calls')::int                                               AS tool_calls,
               (a.metrics->>'prompt_tokens')::int                                            AS prompt_tokens,
               (a.metrics->>'completion_tokens')::int                                        AS completion_tokens,
               (a.metrics->>'total_tokens')::int                                             AS total_tokens,
               (a.metrics->>'truth_verified')::boolean                                       AS truth_verified
        FROM agent_run a
        LEFT JOIN query_job j ON j.request_id = a.request_id{where}
        ORDER BY a.created_at DESC
        LIMIT :limit
    """), {**params, "limit": max(1, limit)}).all()

    result: list[AgentRunRow] = []
    for row in rows:
        data = row._mapping
        llm_calls = _int_or_none(data["llm_calls"])
        completion = _int_or_none(data["completion_tokens"])
        floor = None
        if llm_calls and completion is not None:
            floor = int(completion / llm_calls)
        wall = _int_or_none(data["wall_clock_ms"])
        result.append(AgentRunRow(
            agent_run_id=str(data["id"]),
            model_id=data["model_id"],
            status=str(data["status"]),
            error_code=data["error_code"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            job_id=str(data["job_id"]) if data["job_id"] else None,
            request_id=str(data["request_id"]) if data["request_id"] else None,
            wall_clock_ms=wall if wall is not None and wall >= 0 else None,
            total_duration_ms=_int_or_none(data["total_ms"]),
            agent_duration_ms=_int_or_none(data["agent_ms"]),
            llm_duration_ms=_int_or_none(data["llm_ms"]),
            queue_duration_ms=_int_or_none(data["queue_ms"]),
            verification_duration_ms=_int_or_none(data["verify_ms"]),
            rounds_used=_int_or_none(data["rounds"]),
            sql_calls_used=_int_or_none(data["sql_calls"]),
            llm_calls=llm_calls,
            tool_calls=_int_or_none(data["tool_calls"]),
            prompt_tokens=_int_or_none(data["prompt_tokens"]),
            completion_tokens=completion,
            total_tokens=_int_or_none(data["total_tokens"]),
            truth_verified=data["truth_verified"],
            token_floor=floor,
        ))
    return result


def collect_agent_stats(
    session: Session,
    *,
    window_hours: int = 24,
    model_id: str | None = None,
    limit: int = 20,
) -> AgentStatsResponse:
    """Aggregate per-Agent run statistics over a time window."""
    started = time.perf_counter()
    since = _window_start(window_hours)
    where, params = _where(since, model_id)

    sums_sql = ",\n".join(
        f"  sum((metrics->>'{key}')::numeric) FILTER (WHERE metrics ? '{key}') AS {alias}"
        for alias, key in _SUM_KEYS.items()
    )
    main_rows = session.execute(text(f"""
        SELECT model_id,
               count(*)                                                          AS runs,
               count(*) FILTER (WHERE status = 'completed')                      AS completed,
               count(*) FILTER (WHERE status <> 'completed')                     AS failed,
               count(*) FILTER (WHERE metrics->>'truth_verified' = 'true')       AS truth_verified,
               count(*) FILTER (
                   WHERE metrics->'known_candidate_ids' IS NOT NULL
                     AND (metrics->>'known_candidate_ids')::int > 0
               )                                                                 AS with_candidate_id,
               count(*) FILTER (WHERE metrics ? 'agent_duration_ms')             AS n_agent_duration,
               min(created_at)                                                   AS first_run_at,
               max(created_at)                                                   AS last_run_at,
{sums_sql}
        FROM agent_run{where}
        GROUP BY model_id
        ORDER BY model_id
        LIMIT :limit
    """), {**params, "limit": max(1, limit)}).all()

    percentile_maps: dict[str, dict[str, Percentiles]] = {}
    for name, key in _PERCENTILE_KEYS.items():
        percentile_maps[name], _ = _percentiles(session, key, since, model_id)
    wall_clock_map = _job_wall_clock_percentiles(session, since, model_id)

    coverage_sql = ",\n".join(
        f"  count(*) FILTER (WHERE metrics ? '{key}') AS c{index}"
        for index, key in enumerate(_COVERAGE_KEYS)
    )
    coverage_totals: dict[str, dict[str, int]] = {}
    for row in session.execute(text(f"""
            SELECT model_id, {coverage_sql} FROM agent_run{where} GROUP BY model_id
    """), params).all():
        mapping = row._mapping
        coverage_totals[str(mapping["model_id"])] = {
            key: int(mapping[f"c{index}"] or 0)
            for index, key in enumerate(_COVERAGE_KEYS)
        }

    agents: list[AgentStats] = []
    for row in main_rows:
        data = row._mapping
        model = str(data["model_id"])
        runs = int(data["runs"] or 0)
        completed = int(data["completed"] or 0)
        verified = int(data["truth_verified"] or 0)
        coverage = {key: coverage_totals.get(model, {}).get(key, 0) for key in _COVERAGE_KEYS}

        total_tokens = _int_or_none(data["total_tokens"])
        llm_total = _int_or_none(data["llm_duration_ms"])
        # Derived, and deliberately None when the duration key does not cover the whole
        # window: a rate built from partial coverage would look precise while being wrong.
        duration_coverage = (coverage.get("llm_duration_ms", 0) / runs) if runs else 0.0
        tokens_per_second = None
        if total_tokens and llm_total and duration_coverage >= 0.95:
            tokens_per_second = round(total_tokens / (llm_total / 1000), 3)

        first_run = data["first_run_at"]
        last_run = data["last_run_at"]
        span_hours = (
            round((last_run - first_run).total_seconds() / 3600, 3)
            if first_run is not None and last_run is not None else None
        )

        agents.append(AgentStats(
            model_id=model,
            runs=runs,
            completed=completed,
            failed=int(data["failed"] or 0),
            completion_rate=_rate(completed, runs),
            truth_verified=verified,
            truth_verified_rate=_rate(verified, runs),
            rounds=percentile_maps["rounds"].get(model, Percentiles()),
            sql_calls=percentile_maps["sql_calls"].get(model, Percentiles()),
            llm_calls=percentile_maps["llm_calls"].get(model, Percentiles()),
            tool_calls=percentile_maps["tool_calls"].get(model, Percentiles()),
            wall_clock_ms=wall_clock_map.get(model, Percentiles()),
            total_duration_ms=percentile_maps["total_duration_ms"].get(model, Percentiles()),
            agent_duration_ms=percentile_maps["agent_duration_ms"].get(model, Percentiles()),
            llm_duration_ms=percentile_maps["llm_duration_ms"].get(model, Percentiles()),
            prompt_eval_duration_ms=percentile_maps["prompt_eval_duration_ms"].get(model, Percentiles()),
            generation_duration_ms=percentile_maps["generation_duration_ms"].get(model, Percentiles()),
            queue_duration_ms=percentile_maps["queue_duration_ms"].get(model, Percentiles()),
            verification_duration_ms=percentile_maps["verification_duration_ms"].get(model, Percentiles()),
            fusion_duration_ms=percentile_maps["fusion_duration_ms"].get(model, Percentiles()),
            tokens_per_second=tokens_per_second,
            tokens_per_second_coverage=round(duration_coverage, 4),
            first_run_at=first_run,
            last_run_at=last_run,
            observed_span_hours=span_hours,
            prompt_tokens=_int_or_none(data["prompt_tokens"]),
            completion_tokens=_int_or_none(data["completion_tokens"]),
            total_tokens=total_tokens,
            llm_duration_total_ms=llm_total,
            tool_duration_total_ms=_int_or_none(data["tool_duration_ms"]),
            context_pruned_messages=_int_or_none(data["context_pruned_messages"]),
            duplicate_statements_rejected=_int_or_none(data["duplicate_statements_rejected"]),
            runs_with_usable_candidate_id=int(data["with_candidate_id"] or 0),
            protocol_retries=_int_or_none(data["protocol_retries"]),
            metric_coverage=coverage,
        ))

    by_error_code = {
        str(code or "(none)"): int(n)
        for code, n in session.execute(text(f"""
            SELECT coalesce(error_code, '(none)') AS code, count(*) AS n
            FROM agent_run{where}
            GROUP BY 1 ORDER BY 2 DESC
        """), params).all()
    }

    by_day = [
        DayStats(
            day=day, runs=int(runs or 0), completed=int(completed or 0),
            failed=int(failed or 0), total_tokens=_int_or_none(tokens),
        )
        for day, runs, completed, failed, tokens in session.execute(text(f"""
            SELECT date_trunc('day', created_at) AS day,
                   count(*) AS runs,
                   count(*) FILTER (WHERE status = 'completed') AS completed,
                   count(*) FILTER (WHERE status <> 'completed') AS failed,
                   sum((metrics->>'total_tokens')::numeric) FILTER (WHERE metrics ? 'total_tokens') AS tokens
            FROM agent_run{where}
            GROUP BY 1 ORDER BY 1 DESC LIMIT 30
        """), params).all()
    ]

    # Which loop path actually ran.  False here would mean the serial fallback was used,
    # which changes how the wall clock should be read.
    parallel_runs, serial_runs = session.execute(text(f"""
        SELECT count(*) FILTER (WHERE metrics->>'parallel' = 'true'),
               count(*) FILTER (WHERE metrics->>'parallel' = 'false')
        FROM agent_run{where}
    """), params).one()

    total_runs = sum(agent.runs for agent in agents)
    return AgentStatsResponse(
        window_hours=window_hours,
        since=since,
        model_filter=model_id,
        generated_at=datetime.now(timezone.utc),
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        total_runs=total_runs,
        agents=agents,
        by_error_code=by_error_code,
        by_day=by_day,
        recent_runs=_recent_run_rows(session, since, model_id, limit),
        parallel_runs=int(parallel_runs or 0),
        serial_runs=int(serial_runs or 0),
    )


def collect_job_stats(
    session: Session,
    *,
    window_hours: int = 24,
    limit: int = 10,
) -> JobStatsResponse:
    """Aggregate query-job and event statistics over a time window."""
    started = time.perf_counter()
    since = _window_start(window_hours)
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if since is not None:
        clauses.append("created_at >= :since")
        params["since"] = since
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    by_status = {
        str(status): int(n)
        for status, n in session.execute(text(f"""
            SELECT status, count(*) FROM query_job{where} GROUP BY 1 ORDER BY 2 DESC
        """), params).all()
    }
    by_mode = {
        str(mode or "(unresolved)"): int(n)
        for mode, n in session.execute(text(f"""
            SELECT coalesce(resolved_mode, requested_mode), count(*) FROM query_job{where}
            GROUP BY 1 ORDER BY 2 DESC
        """), params).all()
    }
    by_error_code = {
        str(code or "(none)"): int(n)
        for code, n in session.execute(text(f"""
            SELECT coalesce(error_code, '(none)'), count(*) FROM query_job{where}
            GROUP BY 1 ORDER BY 2 DESC
        """), params).all()
    }

    duration_where = (" WHERE completed_at IS NOT NULL"
                      + (" AND created_at >= :since" if since is not None else ""))
    duration_row = session.execute(text(f"""
        SELECT avg(extract(epoch from (completed_at - started_at)) * 1000)                                       AS avg,
               percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY extract(epoch from (completed_at - started_at)) * 1000)                             AS p50,
               percentile_cont(0.95) WITHIN GROUP (
                   ORDER BY extract(epoch from (completed_at - started_at)) * 1000)                             AS p95,
               max(extract(epoch from (completed_at - started_at)) * 1000)                                      AS maximum
        FROM query_job{duration_where}
    """), params).one()
    duration = Percentiles(
        avg=_float_or_none(duration_row[0]), p50=_float_or_none(duration_row[1]),
        p95=_float_or_none(duration_row[2]), maximum=_float_or_none(duration_row[3]),
    )

    # agent_coverage lives inside the stored payload.  Read it, never recompute it, so
    # the admin view cannot disagree with what the frontend was shown.
    coverage_where = (" WHERE result_payload->'result'->'trace'->>'agent_coverage' IS NOT NULL"
                      + (" AND created_at >= :since" if since is not None else ""))
    coverage_row = session.execute(text(f"""
        SELECT avg((result_payload->'result'->'trace'->>'agent_coverage')::numeric)                     AS avg,
               percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY (result_payload->'result'->'trace'->>'agent_coverage')::numeric)           AS p50,
               percentile_cont(0.95) WITHIN GROUP (
                   ORDER BY (result_payload->'result'->'trace'->>'agent_coverage')::numeric)           AS p95,
               max((result_payload->'result'->'trace'->>'agent_coverage')::numeric)                     AS maximum
        FROM query_job{coverage_where}
    """), params).one()
    coverage = Percentiles(
        avg=_float_or_none(coverage_row[0]), p50=_float_or_none(coverage_row[1]),
        p95=_float_or_none(coverage_row[2]), maximum=_float_or_none(coverage_row[3]),
    )

    # fusion_jobs counts every fusion; zero_coverage counts the subset that produced no
    # verified candidate.  Keeping them apart separates "not a fusion" from "fusion that
    # found nothing", which a single average would blur together.
    mode_clause = "coalesce(resolved_mode, requested_mode) = 'fusion'"
    fusion_jobs = int(session.execute(text(f"""
        SELECT count(*) FROM query_job{where}
        {' AND' if where else ' WHERE'} {mode_clause}
    """), params).scalar() or 0)
    zero_coverage = int(session.execute(text(f"""
        SELECT count(*) FROM query_job{where}
        {' AND' if where else ' WHERE'} {mode_clause}
        AND result_payload->'result'->'trace'->>'agent_coverage' IS NOT NULL
        AND (result_payload->'result'->'trace'->>'agent_coverage')::numeric = 0
    """), params).scalar() or 0)

    event_where = " WHERE e.created_at >= :since" if since is not None else ""
    events_by_type = {
        str(kind): int(n)
        for kind, n in session.execute(text(f"""
            SELECT e.event_type, count(*) FROM query_event e{event_where}
            GROUP BY 1 ORDER BY 2 DESC
        """), params).all()
    }

    recent_rows = session.execute(text(f"""
        SELECT j.id, j.status, coalesce(j.resolved_mode, j.requested_mode), j.error_code,
               extract(epoch from (j.completed_at - j.started_at)) * 1000                        AS duration_ms,
               (j.result_payload->'result'->'trace'->>'agent_coverage')::numeric                 AS coverage,
               (SELECT count(*) FROM query_event e WHERE e.job_id = j.id)                        AS event_count,
               j.created_at
        FROM query_job j{where}
        ORDER BY j.created_at DESC
        LIMIT :limit
    """), {**params, "limit": max(1, limit)}).all()

    recent = [
        JobSummaryRow(
            job_id=str(row[0]), status=str(row[1]), resolved_mode=row[2],
            error_code=row[3],
            duration_ms=_int_or_none(row[4]) if row[4] is not None else None,
            agent_coverage=_float_or_none(row[5]), event_count=int(row[6] or 0),
            created_at=row[7],
        )
        for row in recent_rows
    ]

    total_jobs = sum(by_status.values())
    return JobStatsResponse(
        window_hours=window_hours, since=since, generated_at=datetime.now(timezone.utc),
        elapsed_ms=int((time.perf_counter() - started) * 1000), total_jobs=total_jobs,
        by_status=by_status, by_resolved_mode=by_mode, by_error_code=by_error_code,
        duration_ms=duration, coverage=coverage, events_by_type=events_by_type,
        fusion_jobs=fusion_jobs, fusion_with_zero_coverage=zero_coverage, recent=recent,
    )


__all__ = ["collect_agent_stats", "collect_job_stats"]
