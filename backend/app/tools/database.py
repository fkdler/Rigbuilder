"""query_database() (Plan V2.2 §3.6).

The single entry point that chains validate -> execute -> Observation. The
physical read-only account is the last line of defence; the validator is the
first.
"""

from __future__ import annotations

import difflib
import json
import re
import uuid
from functools import lru_cache
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_readonly_engine
from app.models.truth_v3 import EvidenceClaim
from app.tools.contracts import Observation
from app.tools.errors import error_hint
from app.tools.tables import ALLOWED_TABLES, VIEW_REGISTRY, evidence_fields_for_columns
from app.tools.validator import ValidationFailure, validate_sql

__all__ = ["ALLOWED_TABLES", "query_database", "resolve_evidence"]

# Fields resolve_evidence answers with when the model names none.  These are the
# spec fields the recommendation reasons actually cite; entity.canonical_name is
# deliberately excluded because the catalogue row already carried the name.
#
# Both categories are listed because the default set is applied per entity, not per
# view: a CPU entity holds no gpu.* evidence, so listing the cpu.* fields costs a GPU
# nothing while being the only way a CPU candidate can cite anything at all.  Measured
# against a gpu-only default, a CPU entity fell through to canonical_name only.
DEFAULT_EVIDENCE_FIELDS: tuple[str, ...] = (
    "gpu.architecture",
    "gpu.vram_gib",
    "gpu.memory_type",
    "gpu.board_power_w",
    "gpu.memory_bandwidth_gb_s",
    "cpu.architecture",
    "cpu.socket",
    "cpu.cores_total",
    "cpu.threads",
    "cpu.base_power_w",
)

# Entities one resolve_evidence call may answer for. Measured: nineteen entities in a single
# call produced a 21 306-character tool result - 11 775 tokens against a 14 336-token preflight
# capacity - which no later round could survive, so the run ended as context_budget_exceeded.
RESOLVE_EVIDENCE_ENTITY_LIMIT = 3

# Upper bound on the conjuncts a zero-row diagnosis will probe. Each probe is one extra
# read of the same view, so the cost is capped rather than proportional to the statement.
#
# Measured: a limit of 4 was wrong because the binding constraint was the fifth conjunct
# (``lifecycle_status = 'active'`` at the end of a four-condition WHERE). Capping the count
# rather than the position is what matters, so the first N are expanded into every probe;
# statements with fewer conjuncts than this are fully covered.
ZERO_ROW_PROBE_LIMIT = 8
# Rows the probe counts per conjunct; the answer only needs "some" versus "none".
ZERO_ROW_PROBE_ROWS = 50


def _json_safe(value: Any, max_chars: int) -> Any:
    """Convert database values into bounded JSON primitives for the model."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (dict, list)):
        return value
    text_value = str(value)
    if len(text_value) > max_chars:
        text_value = text_value[:max_chars] + "..."
    return text_value


def _candidate_entity_ids(rows: list[Any]) -> set[UUID]:
    candidates: set[UUID] = set()
    for row in rows:
        for value in row:
            if isinstance(value, UUID):
                candidates.add(value)
            elif isinstance(value, str):
                try:
                    candidates.add(UUID(value))
                except (ValueError, AttributeError):
                    continue
    return candidates


def _restore_returned_entity_ids(engine, rows, columns, views):
    """Resolve only returned keys, through the same registered read-only catalogue view.

    Small models occasionally omit entity_id from an otherwise useful SELECT.
    This adds identity, never candidates or facts, before accepted-evidence lookup.
    An existing entity_id is authoritative and is never silently replaced.
    """
    if "entity_id" in columns or "entity_key" not in columns or not rows:
        return rows, columns
    views = set(views or [])
    if len(views) != 1 or not views.issubset({"cpu_catalog", "gpu_catalog"}):
        return rows, columns
    view = next(iter(views))
    index = columns.index("entity_key")
    keys = list(dict.fromkeys(str(row[index]) for row in rows if row[index]))
    if not keys:
        return rows, columns
    params = {f"key_{n}": key for n,key in enumerate(keys)}
    placeholders = ",".join(":" + name for name in params)
    try:
        with engine.connect() as connection:
            matches = connection.execute(text(
                f"SELECT entity_key, entity_id FROM {view} WHERE entity_key IN ({placeholders})"
            ), params).fetchall()
    except SQLAlchemyError:
        # Leave the original observation intact; verification will fail closed.
        return rows, columns
    mapping = {str(row[0]): str(row[1]) for row in matches}
    return [[mapping.get(str(row[index])), *row] for row in rows], ["entity_id", *columns]


def _reverse_evidence(
    session: Session | None, rows: list[Any], columns: list[str], views: Any = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Resolve evidence IDs and model-facing descriptors in one database query."""
    if session is None or not rows:
        return [], []
    candidates = _candidate_entity_ids(rows)
    if not candidates:
        return [], []
    field_keys = {
        field_key
        for keys in evidence_fields_for_columns(columns, views).values()
        for field_key in keys
    }
    if not field_keys:
        return [], []
    settings = get_settings()
    evidence_rows = session.scalars(
        select(EvidenceClaim).where(
            EvidenceClaim.entity_id.in_(candidates),
            EvidenceClaim.field_key.in_(field_keys),
            EvidenceClaim.review_status == "accepted",
        ).limit(settings.sql_max_evidence_ids)
    ).all()
    evidence_ids: list[str] = []
    details: list[dict[str, Any]] = []
    for row in evidence_rows:
        # Unit tests use a minimal fake session returning UUIDs; details are an
        # additive production feature and safely remain empty for that fake.
        if not isinstance(row, EvidenceClaim):
            evidence_ids.append(str(row))
            continue
        if row.review_status != "accepted":
            continue
        evidence_ids.append(str(row.id))
        details.append({
            "evidence_id": str(row.id),
            "entity_id": str(row.entity_id),
            "field_key": row.field_key,
            "normalized_value": _json_safe(row.normalized_value, settings.sql_max_cell_chars),
            "unit": row.unit_key,
            "accepted": True,
        })
    return (
        sorted(evidence_ids),
        sorted(details, key=lambda item: (item["entity_id"], item["field_key"], item["evidence_id"])),
    )


def _kept_entity_ids(rows: list[list[Any]]) -> set[str]:
    kept: set[str] = set()
    for row in rows:
        for value in row:
            if isinstance(value, str):
                try:
                    kept.add(str(UUID(value)))
                except (ValueError, AttributeError):
                    continue
    return kept


def _estimate_result_chars(
    rows: list[list[Any]],
    evidence: list[dict[str, Any]],
    bindings: list[dict[str, Any]] | None = None,
) -> int:
    """Compact serialized length of every model-facing result collection."""
    return sum(
        len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        for value in (rows, evidence, bindings or [])
    )


def _trim_to_result_budget(
    rows: list[list[Any]],
    columns: list[str],
    evidence_ids: list[str],
    evidence: list[dict[str, Any]],
    settings: Settings,
) -> tuple[int, int, list[str], list[dict[str, Any]]]:
    """Drop tail rows/columns (and their evidence) until the payload fits.

    Returns (rows_dropped, columns_dropped, evidence_ids, evidence).  At least
    one row and one column are always kept so the model still gets a signal.
    """
    budget = settings.tool_max_result_chars
    rows_dropped = 0
    columns_dropped = 0
    while len(rows) > 1 and _estimate_result_chars(rows, evidence) > budget:
        rows.pop()
        rows_dropped += 1
        # Discard proof belonging to discarded rows immediately. Otherwise its
        # unchanged size forces this loop to throw away almost every candidate.
        kept = _kept_entity_ids(rows)
        if any(item.get("entity_id") for item in evidence):
            evidence = [item for item in evidence if item.get("entity_id") in kept]
    while len(columns) > 1 and _estimate_result_chars(rows, evidence) > budget:
        columns.pop()
        columns_dropped += 1
        for row in rows:
            if row:
                row.pop()
    if rows:
        kept = _kept_entity_ids(rows)
        # Only drop evidence when entries carry an entity mapping. The fake
        # evidence-session test path has no details and must keep ids untouched.
        if any(item.get("entity_id") for item in evidence):
            evidence = [item for item in evidence if item.get("entity_id") in kept]
            kept_ids = {str(item["evidence_id"]) for item in evidence}
            evidence_ids = [item for item in evidence_ids if item in kept_ids]
    return rows_dropped, columns_dropped, evidence_ids, evidence


def _build_row_bindings(
    rows: list[list[Any]],
    columns: list[str],
    evidence: list[dict[str, Any]],
    settings: Settings,
    views: Any = None,
) -> list[dict[str, Any]] | None:
    """Bind each result row's field values to same-entity same-field evidence.

    The returned list aligns 1:1 with ``rows``; each element maps
    ``{entity_id, fields: {field_key: {value, unit, evidence_id}}}``. An entry exists only when the
    row's entity has accepted evidence for the same field, so a model copying
    ``evidence_id`` from here can never cross entities or fields.  Returns None
    when no binding is possible (no evidence details, no mapped columns, or the
    serialized payload would exceed the remaining context budget).
    """
    if not rows or not evidence:
        return None
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in evidence:
        entity_id = item.get("entity_id")
        field_key = item.get("field_key")
        if entity_id and field_key:
            index.setdefault((str(entity_id), str(field_key)), []).append(item)
    if not index:
        return None

    # One column can carry more than one field_key when the statement joins views
    # that share a column name (cpu_catalog and gpu_catalog both expose
    # `architecture`); the row's own entity is what selects the right one.
    by_column = evidence_fields_for_columns(columns, views)
    column_fields = [
        (column_index, by_column[column])
        for column_index, column in enumerate(columns)
        if column in by_column
    ]
    if not column_fields:
        return None

    entity_column_index = columns.index("entity_id") if "entity_id" in columns else -1

    def _is_uuid(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        try:
            UUID(value)
            return True
        except (ValueError, AttributeError):
            return False

    def _values_equal(left: Any, right: Any) -> bool:
        if left == right:
            return True
        try:
            return float(left) == float(right)
        except (TypeError, ValueError):
            return False

    bindings: list[dict[str, Any]] = []
    has_any = False
    for row in rows:
        row_map: dict[str, dict[str, Any]] = {}
        entity_id: str | None = None
        if entity_column_index >= 0 and entity_column_index < len(row):
            cell = row[entity_column_index]
            if _is_uuid(cell):
                entity_id = cell
        if entity_id is None:
            for value in row:
                if _is_uuid(value):
                    entity_id = value
                    break
        if entity_id is not None:
            for column_index, field_keys in column_fields:
                if column_index >= len(row):
                    continue
                cell_value = row[column_index]
                for field_key in field_keys:
                    entries = index.get((entity_id, field_key))
                    if not entries:
                        continue
                    chosen = next(
                        (e for e in entries if _values_equal(e.get("normalized_value"), cell_value)),
                        None,
                    )
                    if chosen is None:
                        continue
                    row_map[field_key] = {
                        "value": cell_value,
                        "unit": chosen.get("unit"),
                        "evidence_id": chosen.get("evidence_id"),
                    }
                    has_any = True
        bindings.append({"entity_id": entity_id, "fields": row_map})
    if not has_any:
        return None
    return bindings


def _filter_evidence_for_result(
    rows: list[list[Any]], columns: list[str], evidence: list[dict[str, Any]],
    evidence_ids: list[str], views: Any = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    kept_entities = _kept_entity_ids(rows)
    kept_fields = {
        field_key
        for keys in evidence_fields_for_columns(columns, views).values()
        for field_key in keys
    }
    filtered = [
        item for item in evidence
        if (not item.get("entity_id") or item.get("entity_id") in kept_entities)
        and (not item.get("field_key") or item.get("field_key") in kept_fields)
    ]
    if any(item.get("evidence_id") for item in filtered):
        kept_ids = {str(item["evidence_id"]) for item in filtered}
        evidence_ids = [item for item in evidence_ids if item in kept_ids]
    return filtered, evidence_ids


def _fit_bindings_to_budget(
    rows: list[list[Any]],
    columns: list[str],
    evidence_ids: list[str],
    evidence: list[dict[str, Any]],
    settings: Settings,
    views: Any = None,
) -> tuple[int, int, list[str], list[dict[str, Any]], list[dict[str, Any]] | None]:
    """Fit rows, evidence and bindings inside one shared ToolResult budget."""
    rows_dropped = 0
    columns_dropped = 0
    while True:
        evidence, evidence_ids = _filter_evidence_for_result(rows, columns, evidence, evidence_ids, views)
        bindings = _build_row_bindings(rows, columns, evidence, settings, views)
        if _estimate_result_chars(rows, evidence, bindings) <= settings.tool_max_result_chars:
            return rows_dropped, columns_dropped, evidence_ids, evidence, bindings
        if len(rows) > 1:
            rows.pop()
            rows_dropped += 1
            continue
        if len(columns) > 1:
            columns.pop()
            columns_dropped += 1
            for row in rows:
                if row:
                    row.pop()
            continue
        # Bindings duplicate row values, so discard tail evidence before giving
        # up the final useful row. Evidence IDs are trimmed in lock-step.
        if evidence:
            removed = evidence.pop()
            removed_id = str(removed.get("evidence_id", ""))
            evidence_ids = [item for item in evidence_ids if item != removed_id]
            continue

        # The result budget is a hard safety boundary. A deliberately tiny
        # budget may be smaller than even one JSON row; return an empty result
        # instead of leaking an oversized ToolResult into the model context.
        if rows:
            rows.clear()
            rows_dropped += 1
        if columns:
            columns.clear()
            columns_dropped += 1
        return rows_dropped, columns_dropped, [], [], None


# Names that appear in a statement's FROM/JOIN clause, with any schema qualifier.
_TABLE_REFERENCE = re.compile(
    r"(?:\bFROM\b|\bJOIN\b)\s+([A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)?)",
    re.IGNORECASE,
)


def _forbidden_table_hint(sql: str) -> str | None:
    """Show which of the statement's table references are not registered views.

    Measured: agent-c's query in one run was ``FROM entity_component`` - a table that has
    never existed in this Release - and it then abandoned the run without reading a single
    row, so it never obtained a candidate id. The validator's message says only that a
    table is outside the whitelist; naming the offending reference and the closest
    registered view is what lets the model correct itself.
    """
    referenced = [
        match.group(1).split(".")[-1] for match in _TABLE_REFERENCE.finditer(sql)
    ]
    unknown = [name for name in dict.fromkeys(referenced) if name not in ALLOWED_TABLES]
    if not unknown:
        return None
    parts = []
    for name in unknown[:2]:
        closest = difflib.get_close_matches(name, sorted(ALLOWED_TABLES), n=2, cutoff=0.45)
        if closest:
            parts.append(f"'{name}' is not a registered view; did you mean {closest[0]}?")
        else:
            parts.append(f"'{name}' is not a registered view.")
    return (
        " " + " ".join(parts)
        + " The only readable views are: " + ", ".join(sorted(ALLOWED_TABLES)) + "."
    )


# Table reference with an optional alias, used to know which view a column belongs to.
_FROM_WITH_ALIAS = re.compile(
    r"(?:\bFROM\b|\bJOIN\b)\s+([A-Za-z_][A-Za-z_0-9]*)\s*(?:AS\s+)?([A-Za-z_][A-Za-z_0-9]*)?",
    re.IGNORECASE,
)
# The projection list of a statement, up to the first clause keyword after it.
_SELECT_LIST = re.compile(
    r"\bSELECT\b\s+(.*?)\s*(?:\bFROM\b|\bWHERE\b|\bGROUP\b|\bORDER\b|\bLIMIT\b|\bHAVING\b|$)",
    re.IGNORECASE | re.DOTALL,
)


@lru_cache(maxsize=None)
def _all_registered_columns() -> frozenset[str]:
    """Every column name any registered view exposes."""
    return frozenset(
        column["name"]
        for definition in VIEW_REGISTRY.values()
        for column in definition.get("columns", [])
    )


def _projection_hint(sql: str) -> str | None:
    """Name what is wrong with the SELECT list, when that is knowable before running.

    Measured: agent-c's first query in every run failed on its own SELECT list - once
    writing ``SELECT entity_key, name, 'gpu', market_segment, ...`` where 'gpu' is a string
    literal used as if it were a column, and at other times qualifying with ``gpu.vram_gib``
    when ``gpu`` is neither a table nor an alias. Both are detectable without touching the
    database, and both cost a run its whole budget: no rows come back, so the model never
    obtains a candidate id and invents one instead.
    """
    match = _SELECT_LIST.search(sql)
    if not match:
        return None

    aliases: dict[str, str] = {}
    for view, alias in _FROM_WITH_ALIAS.findall(sql):
        if view not in VIEW_REGISTRY:
            continue
        aliases[view.casefold()] = view
        if alias and alias.casefold() not in _SQL_NOISE:
            aliases[alias.casefold()] = view
    if not aliases:
        return None
    usable = sorted(set(aliases.values()))
    column_names = sorted({name for view in usable for name in _view_columns(view)})

    projection = match.group(1)
    if re.search(r"'[^']*'", projection):
        return (
            " The SELECT list contains a quoted string literal where a column is expected"
            " (for example 'gpu'). A literal selects a constant, not a column; use"
            " gpu_catalog.category instead. Columns available here include: "
            + ", ".join(column_names[:14]) + "."
        )

    unknown = sorted({
        token.split(".")[0].casefold()
        for token in re.findall(
            r"(?<![\w.])([A-Za-z_][A-Za-z_0-9]*\.[A-Za-z_][A-Za-z_0-9]*)", projection
        )
        if token.split(".")[0].casefold() not in aliases
        and token.split(".")[0].casefold() not in column_names
    })
    if unknown:
        return (
            f" The SELECT list qualifies a column with '{unknown[0]}', which is neither a"
            " table in this statement nor one of its aliases. Use the alias you gave the"
            " view, or the view name itself: " + " or ".join(usable) + "."
        )

    # A column that is unknown even when qualified (measured: "e.vram_gib", "e.name") fails
    # the whole statement, so the run reads nothing.
    #
    # Two sets are needed here and they are not interchangeable. Judging whether a bare
    # token *is* a column must use every registered view: an unqualified column is
    # legitimate in a single-view query even when another view also defines it, so a narrow
    # set would invent unknown columns that PostgreSQL would have accepted.
    known_columns = _all_registered_columns()
    # A trailing alias (``expr AS name``) is not a column reference, so it must not be
    # judged. Measured false positive without this: "g.vram_gib AS v" was reported as
    # using the unknown column 'v'.
    projection_without_aliases = re.sub(
        r"\bAS\s+[A-Za-z_][A-Za-z_0-9]*", " ", projection, flags=re.IGNORECASE
    )
    unknown_columns: list[str] = []
    for token in re.findall(r"(?<![\w.])([A-Za-z_][A-Za-z_0-9]*)", projection_without_aliases):
        if token.casefold() in _SQL_NOISE or token.isdigit():
            continue
        if token.casefold() in aliases or token in known_columns:
            continue
        if token.casefold() in unknown_columns:
            continue
        unknown_columns.append(token.casefold())
    if unknown_columns:
        # What is *shown* must come from this statement's own views, never from every
        # registered view. Measured: a single-view gpu_catalog query that named 'pcie_lanes'
        # was answered with 16 candidate columns of which 13 belonged to other views
        # (accessed_at, active_amount, backend, benchmark_run_id, ...), which is an
        # invitation to guess a column that cannot exist in this statement. The suggestion
        # pool is narrowed for the same reason: PostgreSQL rejects a column that only exists
        # in another view, so proposing one sends the model straight back into the failure.
        candidates = sorted(column_names) or sorted(known_columns)
        suggestion = difflib.get_close_matches(
            unknown_columns[0], candidates, n=1, cutoff=0.6
        )
        return (
            f" The SELECT list uses '{unknown_columns[0]}', which is not a column of "
            + " or ".join(usable)
            + ("; did you mean " + suggestion[0] + "?" if suggestion else ".")
            + " Available columns: " + ", ".join(candidates) + "."
        )
    return None


def _execution_detail(exc: SQLAlchemyError) -> str | None:
    """Return the bounded database message for a failed execution.

    The category hint alone ("timeout or database error") gives a model no way to
    tell an ambiguous column from a missing one, so Agents resent the identical
    statement until the round limit. This detail is model-facing only; the public
    execution events keep using error_hint.
    """
    detail = " ".join(str(getattr(exc, "orig", exc)).split())
    return detail[:300] or None


_AMBIGUOUS_MARKERS = ("ambiguous", "不明确")# A bare column reference: an identifier not preceded by a dot and not followed by one.
_COLUMN_REFERENCE = re.compile(r"(?<![\w.])([A-Za-z_][A-Za-z_0-9]*)(?![\w.])")
_SQL_NOISE = frozenset({
    "select", "from", "where", "join", "inner", "left", "right", "full", "outer", "cross",
    "on", "as", "and", "or", "not", "in", "is", "null", "true", "false", "order", "by",
    "group", "having", "limit", "offset", "asc", "desc", "distinct", "with", "union",
    "ilike", "like", "between", "case", "when", "then", "else", "end", "count", "sum",
    "avg", "min", "max", "coalesce", "cast", "text", "int", "numeric", "interval",
})


def _view_aliases(sql: str, views: list[str]) -> dict[str, str]:
    """Map each alias (or bare view name) in this statement to its registered view.

    Written tightly on purpose: an earlier version used a single regex that matched the
    SQL keywords themselves, so the hint prescribed "FROM.entity_key", which is useless
    to the model. Here the alias is only accepted when it directly follows a view name
    (optionally through AS), and anything that is not a plain identifier is rejected.
    """
    aliases: dict[str, str] = {}
    for view in views:
        pattern = rf"(?<![\w.])(?:{re.escape(view)})(?![\w])\s*(?:AS\s+)?([A-Za-z_][A-Za-z_0-9]*)?"
        for match in re.finditer(pattern, sql, re.IGNORECASE):
            alias = match.group(1)
            if alias and alias.casefold() not in _SQL_NOISE:
                aliases.setdefault(alias, view)
        aliases.setdefault(view, view)
    return aliases


def _ambiguous_column_hint(sql: str, detail: str | None) -> str | None:
    """Prescribe the exact qualified form for a column the database called ambiguous.

    Measured motivation: agent-c's first query failed in every run and it then resent
    the identical statement three times without ever obtaining an entity id, so every
    submission it made was an invented UUID. Two properties matter more than detail
    here. First, only the views the query itself mentions are useful: an earlier version
    listed all seventeen views carrying entity_key, which both overflowed the 300
    character budget and buried the answer. Second, the message must be short and
    prescriptive, because the model acts on a concrete form like g.entity_key far more
    reliably than on an explanation.
    """
    if not detail or not any(marker in detail for marker in _AMBIGUOUS_MARKERS):
        return None

    mentioned = [
        name for name in sorted(VIEW_REGISTRY)
        if re.search(rf"(?<![\w.]){re.escape(name)}(?![\w])", sql, re.IGNORECASE)
    ]
    if len(mentioned) < 2:
        # Nothing in the statement can be qualified against a second view, so a hint
        # would only add noise.
        return None

    aliases: dict[str, str] = {}
    for alias, view in re.findall(
        rf"(?<![\w.])([A-Za-z_][A-Za-z_0-9]*)\s+(?:AS\s+)?({ '|'.join(mentioned) })(?![\w])",
        sql, re.IGNORECASE,
    ):
        aliases.setdefault(alias, view)

    aliases: dict[str, str] = {}
    for alias, view in re.findall(
        rf"(?<![\w.])([A-Za-z_][A-Za-z_0-9]*)\s+(?:AS\s+)?({ '|'.join(mentioned) })(?![\w])",
        sql, re.IGNORECASE,
    ):
        aliases.setdefault(alias, view)

    aliases = _view_aliases(sql, mentioned)

    candidates = {
        match.group(1)
        for match in _COLUMN_REFERENCE.finditer(sql)
        if match.group(1).casefold() not in _SQL_NOISE
    }
    for column in sorted(candidates):
        owners = [view for view in mentioned if column in _view_columns(view)]
        if len(owners) < 2:
            continue
        forms = []
        for owner in owners:
            for alias, view in aliases.items():
                if view == owner:
                    forms.append(f"{alias}.{column}")
                    break
            else:
                forms.append(f"{owner}.{column}")
        return (" Both views in this query carry that column, so it must be qualified. "
                "Use one of: " + " or ".join(forms[:3]) + ".")
    return None


def _split_conjuncts(where: str) -> list[str] | None:
    """Split a WHERE body on top-level AND, or None when it is not safely splittable.

    Parentheses and OR make the split ambiguous (``a = 1 OR b = 2`` is one conjunct, and
    splitting it would change the meaning), so those statements are left alone rather than
    probed with a condition the model never wrote.
    """
    if "(" in where or ")" in where:
        return None
    if re.search(r"\bOR\b", where, re.IGNORECASE):
        return None
    parts = [part.strip() for part in re.split(r"\bAND\b", where, flags=re.IGNORECASE)]
    parts = [part for part in parts if part]
    return parts or None


def _conjunct_spans(body: str, conjuncts: list[str]) -> list[tuple[int, int]] | None:
    """Character spans of each conjunct's own text inside the WHERE body, in order.

    Walking the body once (rather than searching the whole statement) keeps a condition from
    matching inside the projection. Each span covers the condition text only - the ``AND``
    that precedes it is deliberately excluded, so replacing a condition with TRUE leaves the
    connectors untouched; an earlier version included it and produced "WHERE x = 1 TRUE AND
    y", which PostgreSQL rejects.
    """
    spans: list[tuple[int, int]] = []
    cursor = 0
    for index, conjunct in enumerate(conjuncts):
        start = cursor
        if index > 0:
            match = re.compile(r"\bAND\b", re.IGNORECASE).search(body, cursor)
            if match is None:
                return None
            start = match.end()
        found = body.find(conjunct, start)
        if found < 0:
            return None
        spans.append((found, found + len(conjunct)))
        cursor = found + len(conjunct)
    return spans


def _statement_without_conjunct(sql: str, spans: list[tuple[int, int]], body_start: int,
                                index: int) -> str:
    """The statement with one conjunct replaced by TRUE."""
    start, end = spans[index]
    return sql[:body_start + start] + "TRUE" + sql[body_start + end:]


def _zero_row_hint(
    sql: str,
    readonly_engine: Engine | None = None,
) -> str | None:
    """Explain which condition emptied a zero-row result, by counting without it.

    Measured: agent-c asked for a device to run a 17B model six times, relaxing only the
    numeric bound each time (``vram_gib >= 24`` down to ``>= 4``) while a single categorical
    conjunct, ``lifecycle_status = 'active'``, held the result at zero for every attempt -
    40 of 41 GPUs carry ``unknown``. It spent its whole SQL budget, obtained no candidate id
    and fabricated one. The statement *succeeded*, so no error path could tell it that the
    data was there and one condition was hiding it.

    The probe re-runs the model's own statement with exactly one conjunct replaced by TRUE,
    so it can only read what the statement itself reads and needs no schema rewriting. It
    runs on the read-only engine - the only account allowed to see ``agent_catalog`` - and
    each probe opens its own connection, because a failed statement aborts the transaction
    and would otherwise take every later probe down with it.

    Returns None whenever the statement is not safely splittable, the engine is missing, or
    no single conjunct explains the empty result. A hint is never invented.
    """
    if readonly_engine is None:
        return None
    if re.search(r"\b(?:UNION|INTERSECT|EXCEPT)\b", sql, re.IGNORECASE):
        return None
    where_match = re.search(r"\bWHERE\b", sql, re.IGNORECASE)
    if where_match is None:
        return None
    body_start = where_match.end()
    body = sql[body_start:]
    body = re.split(
        r"\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|\bHAVING\b|\bOFFSET\b|\bFETCH\b",
        body, maxsplit=1, flags=re.IGNORECASE,
    )[0]
    conjuncts = _split_conjuncts(body)
    if not conjuncts or len(conjuncts) < 2:
        return None
    spans = _conjunct_spans(body, conjuncts)
    if spans is None:
        return None
    probed = conjuncts[:ZERO_ROW_PROBE_LIMIT]

    counts: list[tuple[str, int]] = []
    for index, _ in enumerate(probed):
        candidate = _statement_without_conjunct(sql, spans, body_start, index)
        try:
            with readonly_engine.connect() as connection:
                value = connection.execute(text(candidate)).fetchmany(ZERO_ROW_PROBE_ROWS + 1)
        except SQLAlchemyError:
            # The probe is optional by construction: a diagnostic that fails must leave the
            # empty result as it was rather than turn a success into an error.
            continue
        counts.append((_describe_conjunct(probed[index]), len(value)))

    if not counts:
        return None

    # Report the conditions whose removal alone makes the statement return rows: that is the
    # actionable fact, because dropping exactly one condition turns the answer from nothing
    # into candidates. Reporting "most rows freed" instead would point at a loose condition
    # (a cardinality filter freeing 24 rows) while the binding one freed only 6, which is the
    # opposite of what the model needs to change.
    unblocking = [(label, count) for label, count in counts if count > 0]
    if unblocking:
        label, count = max(unblocking, key=lambda item: item[1])
        others = [text_ for text_, _ in unblocking if text_ != label]
        extra = ""
        if len(others) == 1:
            extra = f" Dropping {others[0]} alone would also return rows."
        elif len(others) > 1:
            extra = f" Dropping any of {', '.join(others)} alone would also return rows."
        return (
            f" This query returned no rows, but the data is not missing: removing the condition"
            f" {label} makes the same query return candidates. That single condition is what"
            f" empties the result, so relax it rather than tightening the numeric bounds.{extra}"
        )

    return (
        " This query returned no rows, and no single condition is responsible: removing any one"
        " of them still matches nothing. The combination itself is not in the data, so check the"
        " values against the data dictionary in the system prompt and ask for fewer conditions"
        " at once."
    )


def _describe_conjunct(conjunct: str) -> str:
    """Render one WHERE conjunct compactly for the hint."""
    compact = " ".join(conjunct.split())
    return compact if len(compact) <= 90 else compact[:87] + "..."



@lru_cache(maxsize=None)
def _view_columns(view: str) -> frozenset[str]:
    definition = VIEW_REGISTRY.get(view) or {}
    return frozenset(column["name"] for column in definition.get("columns", []))


def query_database(sql: str, *, evidence_session: Session | None = None) -> Observation:
    """Validate and execute one model-generated SQL query against the read-only DB."""
    settings = get_settings()

    validation = validate_sql(sql, settings)
    if isinstance(validation, ValidationFailure):
        # A rejected statement used to come back with only a code and a generic hint, so a
        # model that named an unregistered table had nothing to correct against. Measured:
        # agent-c wrote "FROM entity_component" and then abandoned the run.
        detail = (
            _forbidden_table_hint(sql)
            if validation.error_code in {"forbidden_table", "forbidden_function"}
            else None
        )
        return Observation(
            success=False,
            error_code=validation.error_code,
            error_hint=validation.error_hint,
            error_detail=detail,
        )

    readonly_engine = get_readonly_engine()
    if readonly_engine is None:
        return Observation(
            success=False,
            error_code="database_not_configured",
            error_hint=error_hint("database_not_configured"),
        )

    query_id = str(uuid.uuid4())
    try:
        with readonly_engine.connect() as connection:
            result = connection.execute(text(validation.sql))
            rows = result.fetchmany(settings.sql_max_rows + 1)
            columns = list(result.keys())
    except SQLAlchemyError as exc:
        detail = _execution_detail(exc)
        # Neither of these needs the database: they read the statement's own projection and
        # the registered view definitions, and they turn an opaque execution error into the
        # correction the model needs.
        for hint in (_projection_hint(validation.sql), _ambiguous_column_hint(validation.sql, detail)):
            if hint:
                detail = (detail or "") + hint
                break
        return Observation(
            success=False,
            query_id=query_id,
            error_code="execution_error",
            error_hint=error_hint("execution_error"),
            error_detail=detail,
        )

    row_cap_exceeded = len(rows) > settings.sql_max_rows
    rows = rows[: settings.sql_max_rows]
    # The validated statement names the views it reads, so the column -> field_key
    # mapping can be scoped to them. Without it a CPU view's `architecture` column
    # would be mapped to `gpu.architecture` and lose its evidence binding.
    # getattr: tests substitute a lightweight validation stand-in, and an unknown
    # view set degrades to the unscoped (union) mapping rather than failing.
    views = getattr(validation, "tables", None)
    if evidence_session is not None:
        rows, columns = _restore_returned_entity_ids(readonly_engine, rows, columns, views)
    evidence_ids, evidence = _reverse_evidence(evidence_session, rows, columns, views)
    json_rows = [[_json_safe(value, settings.sql_max_cell_chars) for value in row] for row in rows]
    # One pass accounts for rows, evidence AND bindings together, filtering proof
    # at every step; a preliminary pass used to collapse broad results to one row.
    rows_dropped, columns_dropped, evidence_ids, evidence, row_bindings = _fit_bindings_to_budget(
        json_rows, columns, evidence_ids, evidence, settings, views
    )
    result_budget_trimmed = rows_dropped > 0 or columns_dropped > 0
    truncated = row_cap_exceeded or result_budget_trimmed
    truncated_reason: str | None = None
    reduction_hint: str | None = None
    if row_cap_exceeded:
        truncated_reason = "row_limit"
        reduction_hint = (
            f"The query matched more than {settings.sql_max_rows} rows. Re-run a narrower "
            "query (tighter WHERE / LIMIT / fewer columns) to inspect specific candidates."
        )
    if result_budget_trimmed:
        truncated_reason = "result_size"
        reduction_hint = (
            f"The result exceeded the {settings.tool_max_result_chars}-character context budget; "
            "returned rows/columns were trimmed. Re-run a narrower query with fewer rows or columns."
        )

    if reduction_hint is None:
        # A malformed projection is not always an error: PostgreSQL happily runs
        # ``SELECT 'gpu', name FROM gpu_catalog`` and returns the constant 'gpu' in that
        # column. The rows then look plausible while carrying a wrong value, and nothing
        # else in the pipeline can notice. Measured on agent-c, which wrote exactly this.
        projection = _projection_hint(validation.sql)
        if projection:
            reduction_hint = projection.strip()

    # A successful statement that matched nothing carries no error, so without this the
    # model cannot tell "the data is absent" from "one condition is too narrow" and keeps
    # moving the bound it can see. Measured: six attempts at a 17B-model question, every one
    # of them returning zero rows for the same categorical conjunct.
    zero_row_hint: str | None = None
    if not json_rows:
        zero_row_hint = _zero_row_hint(validation.sql, readonly_engine)

    from app.services.catalogue_position import tool_context
    decision_context = tool_context(evidence_session, sorted(_kept_entity_ids(json_rows)))
    # Calculations are optional when the result budget is exhausted. Never
    # replace fact evidence with derived metadata or silently exceed the budget.
    if decision_context and _estimate_result_chars(json_rows, evidence, row_bindings) + len(json.dumps(decision_context, ensure_ascii=False)) > settings.tool_max_result_chars:
        # Keep candidate facts, dates and uncertainty even if comparison proof
        # does not fit. Withhold ranks whose full comparison provenance is omitted.
        decision_context["calculations"] = {}
        for candidate in decision_context["candidates"]:
            candidate["position"] = {**candidate["position"], "dimensions": {},
                                     "omitted_reason": "comparison evidence exceeds result budget"}
        if _estimate_result_chars(json_rows, evidence, row_bindings) + len(json.dumps(decision_context, ensure_ascii=False)) > settings.tool_max_result_chars:
            # Row bindings already carry field values and evidence. Reference
            # them instead of sending the same facts a second time.
            for candidate in decision_context["candidates"]:
                candidate["facts"] = {}
                candidate["facts_reference"] = "row_bindings"
                candidate["confidence"]["meaning"] = "fact coverage, not performance"
                candidate["office_fit"]["meaning"] = "policy utility, not performance"
                candidate["position"]["label"] = "specifications only"
            decision_context["omitted_reason"] = "duplicate facts referenced through row_bindings; comparison proof omitted"
        while len(json_rows) > 1 and _estimate_result_chars(json_rows, evidence, row_bindings) + len(json.dumps(decision_context, ensure_ascii=False)) > settings.tool_max_result_chars:
            json_rows.pop()
            evidence, evidence_ids = _filter_evidence_for_result(json_rows, columns, evidence, evidence_ids, views)
            row_bindings = _build_row_bindings(json_rows, columns, evidence, settings, views)
            kept_ids = _kept_entity_ids(json_rows)
            decision_context["candidates"] = [c for c in decision_context["candidates"] if c["entity_id"] in kept_ids]
            truncated, truncated_reason = True, "result_size"
            reduction_hint = "Decision metadata and evidence exceeded the result budget; query fewer rows/columns."
        if _estimate_result_chars(json_rows, evidence, row_bindings) + len(json.dumps(decision_context, ensure_ascii=False)) > settings.tool_max_result_chars:
            decision_context = {"policy_version": decision_context["policy_version"],
                "release_key": decision_context["release_key"], "data_cutoff": decision_context["data_cutoff"],
                "candidates": [], "omitted_reason": "single-row payload exhausted metadata budget"}
    return Observation(
        success=True,
        decision_context=decision_context,
        columns=columns,
        rows=json_rows,
        row_count=len(json_rows),
        truncated=truncated,
        truncated_reason=truncated_reason,
        reduction_hint=reduction_hint,
        query_id=query_id,
        evidence_ids=evidence_ids,
        evidence=evidence,
        row_bindings=row_bindings,
        zero_row_hint=zero_row_hint,
    )


def _evidence_for_entities(
    session: Session | None, entity_ids: list[UUID], limit: int,
) -> dict[str, list[dict[str, Any]]]:
    """All accepted evidence for these entities, grouped by entity id.

    Unlike :func:`_reverse_evidence`, which can only surface evidence for columns
    the model happened to SELECT, this returns every field the entity has evidence
    for.  That matters: an RTX 4060 carries accepted evidence for ten fields
    (architecture, board_power_w, memory_bandwidth_gb_s, memory_bus_width_bit,
    memory_type, pcie_generation, pcie_lanes, vram_gib, ecc_support and
    canonical_name), but a query that selects only entity_key/name/vram_gib can
    reach just one of them.
    """
    if session is None or not entity_ids:
        return {}
    rows = session.scalars(
        select(EvidenceClaim).where(
            EvidenceClaim.entity_id.in_(entity_ids),
            EvidenceClaim.review_status == "accepted",
        ).limit(limit)
    ).all()
    settings = get_settings()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, EvidenceClaim):
            continue
        grouped.setdefault(str(row.entity_id), []).append({
            "field_key": row.field_key,
            "value": _json_safe(row.normalized_value, settings.sql_max_cell_chars),
            "unit": row.unit_key,
            "evidence_id": str(row.id),
        })
    for entries in grouped.values():
        entries.sort(key=lambda item: (item["field_key"], item["evidence_id"]))
    return grouped


def resolve_evidence(
    entity_keys: list[str], *, view: str = "component_profile_catalog",
    fields: list[str] | None = None,
    evidence_session: Session | None = None,
) -> Observation:
    """Resolve real entity ids and their accepted evidence for the given keys.

    The Agent supplies the ``entity_key`` values it read from a catalogue view and
    receives the canonical UUID plus one entry per field that has accepted
    evidence.  Every identifier returned here comes from the Truth DB: nothing is
    computed, inferred or invented, so a model that copies from this result cannot
    cite a nonexistent candidate or a mismatched evidence id.

    Two engines are used on purpose.  The key-to-UUID lookup goes through the
    registered ``agent_catalog`` view on the read-only account, which is the same
    boundary ``query_database`` obeys (that account has no rights on the ``truth``
    schema at all).  The evidence reverse lookup uses the application session,
    exactly as :func:`query_database` does when it attaches evidence to a result.
    """
    settings = get_settings()
    keys = [str(key).strip() for key in entity_keys if str(key).strip()]
    if not keys:
        return Observation(
            success=False,
            error_code="invalid_tool_call",
            error_hint="resolve_evidence requires at least one entity_key.",
        )
    if len(keys) > RESOLVE_EVIDENCE_ENTITY_LIMIT:
        # Measured: resolving nineteen entities in one call returned 21 306 characters
        # (11 775 tokens) - 82% of a 14 336-token preflight capacity in a single tool
        # result, which made the next round impossible and ended the run as
        # context_budget_exceeded. The model can resolve the rest in a second call, and the
        # truncation is reported below so it knows to.
        entity_limit_hit = True
        keys = keys[:RESOLVE_EVIDENCE_ENTITY_LIMIT]
    else:
        entity_limit_hit = False
    if view not in ALLOWED_TABLES:
        return Observation(
            success=False,
            error_code="forbidden_table",
            error_hint=error_hint("forbidden_table"),
            error_detail=f"'{view}' is not a registered agent_catalog view.",
        )

    readonly_engine = get_readonly_engine()
    if readonly_engine is None:
        return Observation(
            success=False,
            error_code="database_not_configured",
            error_hint=error_hint("database_not_configured"),
        )

    placeholders = ", ".join(f":key{index}" for index in range(len(keys)))
    parameters = {f"key{index}": key for index, key in enumerate(keys)}
    try:
        with readonly_engine.connect() as connection:
            found = connection.execute(
                text(
                    f"SELECT entity_id::text AS entity_id, entity_key FROM {view} "
                    f"WHERE entity_key IN ({placeholders})"
                ),
                parameters,
            ).fetchall()
    except SQLAlchemyError as exc:
        return Observation(
            success=False,
            error_code="execution_error",
            error_hint=error_hint("execution_error"),
            error_detail=_execution_detail(exc),
        )

    by_key: dict[str, str] = {}
    for row in found:
        if row.entity_id is not None:
            by_key[str(row.entity_key)] = str(row.entity_id)
    entity_ids = [UUID(value) for value in dict.fromkeys(by_key.values())]
    grouped = _evidence_for_entities(evidence_session, entity_ids, settings.sql_max_evidence_ids)
    wanted = {str(key) for key in (fields or []) if str(key).strip()}
    if not wanted:
        # Every entity carries entity.canonical_name evidence, but the model already
        # read the name from the catalogue row, so repeating it is pure context cost.
        # Measured: resolving 19 entities returned 70 entries and pushed the next
        # prompt to 17 531 tokens against a 16 384 window. The measured budget is
        # enforced below so a wide resolve cannot overflow the conversation.
        wanted = set(DEFAULT_EVIDENCE_FIELDS)

    json_rows: list[list[Any]] = []
    evidence: list[dict[str, Any]] = []
    evidence_ids: list[str] = []
    unresolved: list[str] = []
    truncated = False
    for key in keys:
        entity_id = by_key.get(key)
        if entity_id is None:
            unresolved.append(key)
            continue
        entries = [entry for entry in grouped.get(entity_id, []) if entry["field_key"] in wanted]
        json_rows.append([key, entity_id, len(entries)])
        for entry in entries:
            evidence.append({
                "entity_id": entity_id,
                "entity_key": key,
                "field_key": entry["field_key"],
                "normalized_value": entry["value"],
                "unit": entry["unit"],
                "evidence_id": entry["evidence_id"],
            })
            evidence_ids.append(entry["evidence_id"])
            if len(evidence) >= settings.sql_max_evidence_ids:
                truncated = True
                break
        if truncated:
            break

    columns = ["entity_key", "entity_id", "evidence_count"]
    hint_parts: list[str] = []
    if entity_limit_hit:
        hint_parts.append(
            f"Only the first {RESOLVE_EVIDENCE_ENTITY_LIMIT} entity_key values were resolved, "
            "because answering for more produces a result too large to keep in context. "
            "Resolve the remaining candidates in a separate call once you have chosen them."
        )
    if unresolved:
        hint_parts.append(
            "entity_key not present in " + view + ": " + ", ".join(unresolved[:10])
        )
    if truncated:
        hint_parts.append(
            "The evidence list hit its cap; resolve fewer entities at a time, or name the "
            "fields you will cite so the answer stays inside the context budget."
        )
    if not evidence:
        hint_parts.append(
            "No accepted evidence was found for these entities. Fact claims need one; "
            "submit prices and reasoning instead, or choose candidates that have evidence."
        )
    reduction_hint = " ".join(hint_parts) or None

    return Observation(
        success=True,
        columns=columns,
        rows=json_rows,
        row_count=len(json_rows),
        truncated=truncated or entity_limit_hit,
        truncated_reason=(
            "entity_limit" if entity_limit_hit
            else ("evidence_cap" if truncated else None)
        ),
        reduction_hint=reduction_hint,
        evidence=evidence,
        evidence_ids=sorted(set(evidence_ids)),
    )
