"""Read-only introspection of the database and the two inference endpoints.

Two separate questions, deliberately kept apart:

* what the database currently holds (release, schema size, per-view row counts); and
* what the two llama-servers are doing right now (reachable, loaded model,
  capability state).

A failure in either half degrades that half to "unavailable".  Neither may raise into
the request: an operator console that goes blank when one view is missing is worse than
one that says which view is missing.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.admin.models import (
    DatabaseOverviewResponse,
    EndpointStatus,
    EndpointsResponse,
    TableRowCount,
    ViewRowCount,
)

# Row counts above this are taken from pg_class.reltuples and flagged as estimates, so a
# large corpus cannot make the console slow.  The current corpus is far below it.
_EXACT_COUNT_LIMIT = 50_000

# The truth tables whose emptiness changes what the product can answer.  A zero here is
# not a schema problem, it is a data-collection gap, and it is worth surfacing by name.
_KEY_TABLES: tuple[str, ...] = (
    "catalog_entity", "hardware", "ai_model", "model_variant",
    "evidence_claim", "price_snapshot", "benchmark_run", "benchmark_subject",
    "runtime_support_snapshot", "entity_attribute", "compatibility_edge",
    "rule_definition", "model_capability", "workload_profile", "source_document",
)


def _reltuples(session: Session, qualified: str) -> int | None:
    schema, _, name = qualified.partition(".")
    value = session.execute(text("""
        SELECT reltuples::bigint FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = :schema AND c.relname = :name
    """), {"schema": schema, "name": name}).scalar()
    return int(value) if value is not None and value >= 0 else None


def _count_relation(session: Session, qualified: str) -> tuple[int | None, bool, str | None]:
    """Row count for one relation: exact when small, estimated when large.

    Returns ``(rows, estimated, error)``.  A missing relation or a denied read is
    reported as an error string rather than raised.
    """
    try:
        estimate = _reltuples(session, qualified)
    except Exception as exc:  # noqa: BLE001 - a name we cannot resolve must not 500
        return None, False, f"{type(exc).__name__}"
    if estimate is not None and estimate > _EXACT_COUNT_LIMIT:
        return estimate, True, None
    try:
        return int(session.execute(text(f"SELECT count(*) FROM {qualified}")).scalar() or 0), False, None
    except Exception as exc:  # noqa: BLE001
        return None, False, f"{type(exc).__name__}"


def collect_database_overview(
    session: Session,
    *,
    max_views: int = 32,
    max_tables: int = 40,
) -> DatabaseOverviewResponse:
    """Describe the database without reading any business rows."""
    started = time.perf_counter()
    warnings: list[str] = []
    reachable = True

    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return DatabaseOverviewResponse(
            generated_at=datetime.now(timezone.utc),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            reachable=False,
            warnings=[f"database_unreachable: {type(exc).__name__}"],
        )

    release_key = release_status = None
    try:
        row = session.execute(text("""
            SELECT release_key, status FROM truth.dataset_release
            ORDER BY created_at DESC LIMIT 1
        """)).one_or_none()
        if row is not None:
            release_key, release_status = str(row[0]), str(row[1])
        else:
            warnings.append("no_release_row: truth.dataset_release is empty")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"release_unavailable: {type(exc).__name__}")

    alembic_version = None
    try:
        alembic_version = session.execute(
            text("SELECT version_num FROM alembic_version")).scalar()
        alembic_version = str(alembic_version) if alembic_version else None
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"alembic_version_unavailable: {type(exc).__name__}")

    try:
        view_names = [
            str(name) for name in session.execute(text("""
                SELECT table_name FROM information_schema.views
                WHERE table_schema = 'agent_catalog' ORDER BY table_name
            """)).scalars().all()
        ]
    except Exception as exc:  # noqa: BLE001
        view_names = []
        warnings.append(f"view_list_unavailable: {type(exc).__name__}")

    views: list[ViewRowCount] = []
    for name in view_names[:max_views]:
        rows, estimated, error = _count_relation(session, f"agent_catalog.{name}")
        views.append(ViewRowCount(view=name, rows=rows, estimated=estimated, error=error))

    try:
        table_names = [
            str(name) for name in session.execute(text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'truth' AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """)).scalars().all()
        ]
    except Exception as exc:  # noqa: BLE001
        table_names = []
        warnings.append(f"table_list_unavailable: {type(exc).__name__}")

    key_tables: list[TableRowCount] = []
    for name in _KEY_TABLES[:max_tables]:
        if table_names and name not in table_names:
            key_tables.append(TableRowCount(table=name, rows=None, error="missing"))
            continue
        rows, estimated, error = _count_relation(session, f"truth.{name}")
        key_tables.append(TableRowCount(table=name, rows=rows, estimated=estimated, error=error))

    recommendable = None
    try:
        recommendable = int(session.execute(
            text("SELECT count(*) FROM truth.catalog_entity WHERE recommendable")).scalar() or 0)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"recommendable_unavailable: {type(exc).__name__}")

    return DatabaseOverviewResponse(
        generated_at=datetime.now(timezone.utc),
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        reachable=reachable,
        release_key=release_key,
        release_status=release_status,
        alembic_version=alembic_version,
        agent_view_count=len(view_names),
        truth_table_count=len(table_names),
        recommendable_entities=recommendable,
        views=views,
        empty_views=[view.view for view in views if view.rows == 0],
        key_tables=key_tables,
        empty_key_tables=[item.table for item in key_tables if item.rows == 0],
        warnings=warnings,
    )


async def collect_endpoint_status(
    *,
    settings: Any = None,
    timeout_seconds: float = 5.0,
    overall_timeout_seconds: float = 12.0,
) -> EndpointsResponse:
    """Probe every configured profile and report live endpoint state."""
    started = time.perf_counter()
    from app.core.config import get_settings
    from app.inference.profiles import (
        build_profiles,
        parallel_configuration_issues,
        settings_parallel_capable,
    )
    from app.inference.servers import probe_profiles

    resolved = settings or get_settings()
    from app.inference.context_window import refresh_context_windows
    profiles = await refresh_context_windows(resolved)
    warnings: list[str] = []

    probes: dict[str, Any] = {}
    try:
        # A hung endpoint must not hold the console open; the cap is on the whole gather.
        probes = await asyncio.wait_for(
            probe_profiles(profiles, resolved, timeout_seconds=timeout_seconds),
            timeout=overall_timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - a hung or broken endpoint must not 500 the console
        warnings.append(f"probe_failed: {type(exc).__name__}")

    capability_state: dict[str, str] = {}
    try:
        from app.agents.capabilities import capability_store
        capability_state = {profile.id: capability_store.state(profile.id) for profile in profiles}
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"capability_store_unavailable: {type(exc).__name__}")

    statuses: list[EndpointStatus] = []
    for profile in profiles:
        probe = probes.get(profile.id)
        statuses.append(EndpointStatus(
            profile_id=profile.id,
            endpoint_url=profile.endpoint_url,
            configured_model_id=profile.model_id,
            reachable=bool(probe.reachable) if probe is not None else False,
            latency_ms=probe.latency_ms if probe is not None else None,
            error=probe.error if probe is not None else "probe_not_run",
            loaded_models=list(probe.loaded_models) if probe is not None else [],
            capability_state=capability_state.get(profile.id, "unverified"),
            context_size=profile.context_size,
            context_source=profile.context_source,
            configured_context_size=profile.configured_context_size,
            max_tool_rounds=profile.max_tool_rounds,
            max_sql_calls=profile.max_sql_calls,
            terminal_tool_supported=profile.terminal_tool_supported,
            is_local=profile.is_endpoint_local,
        ))

    try:
        issues = list(parallel_configuration_issues(resolved))
        capable = bool(settings_parallel_capable(resolved))
    except Exception as exc:  # noqa: BLE001
        issues = [f"configuration_check_failed: {type(exc).__name__}"]
        capable = False

    return EndpointsResponse(
        generated_at=datetime.now(timezone.utc),
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        profiles=statuses,
        distinct_endpoints=len({item.endpoint_url for item in statuses}) == len(statuses) and bool(statuses),
        distinct_model_ids=len({item.configured_model_id for item in statuses}) == len(statuses) and bool(statuses),
        configuration_issues=issues,
        parallel_capable=capable,
        warnings=warnings,
        agent_terminal_tool_supported=getattr(resolved, "agent_terminal_tool_supported", None),
        agent_parallel_required=getattr(resolved, "agent_parallel_required", None),
        capability_smoke_on_startup=getattr(resolved, "capability_smoke_on_startup", None),
        agent_max_rounds=getattr(resolved, "agent_max_rounds", None),
        agent_max_sql_calls=getattr(resolved, "agent_max_sql_calls", None),
    )


__all__ = ["collect_database_overview", "collect_endpoint_status"]
