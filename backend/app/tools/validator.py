"""SQL Parser + Validator (Plan V2.2 §3.4).

Five-stage pure pipeline with no database access:

    1. length pre-check
    2. pglast parse
    3. exactly one top-level SELECT/WITH statement
    4. AST walk: table whitelist, forbidden functions, resource limits
    5. canonical rewrite with an automatic LIMIT

The returned SQL is safe to execute through the agent_readonly engine, but the
read-only engine remains the physical backstop (defence in depth).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pglast import parse_sql
from pglast.stream import RawStream

from app.core.config import Settings
from app.tools.errors import ErrorCategory, classify, error_hint
from app.tools.tables import ALLOWED_TABLES

FORBIDDEN_FUNCTIONS = frozenset(
    {
        "pg_sleep",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_write_file",
        "pg_write_binary_file",
        "pg_ls_dir",
        "pg_ls_logdir",
        "pg_ls_waldir",
        "pg_stat_file",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "lo_import",
        "lo_export",
        "lo_create",
        "lo_unlink",
        "dblink_exec",
        "dblink_connect",
        "dblink_send_query",
    }
)

ALLOWED_FUNCTIONS = frozenset(
    {"abs", "avg", "ceil", "ceiling", "coalesce", "count", "date_trunc", "extract",
     "floor", "greatest", "least", "length", "lower", "max", "min", "nullif",
     "round", "sum", "upper"}
)


@dataclass(frozen=True)
class ValidationSuccess:
    sql: str
    tables: frozenset[str]


@dataclass(frozen=True)
class ValidationFailure:
    error_code: str
    error_hint: str
    category: ErrorCategory
    details: dict[str, Any] = field(default_factory=dict)


ValidationOutcome = ValidationSuccess | ValidationFailure


@dataclass
class _Stats:
    tables: set[str] = field(default_factory=set)
    cte_names: set[str] = field(default_factory=set)
    functions: set[str] = field(default_factory=set)
    function_schemas: set[str] = field(default_factory=set)
    joins: int = 0
    max_subquery_depth: int = 0
    forbidden_schemas: set[str] = field(default_factory=set)


def _failure(error_code: str, **details: Any) -> ValidationFailure:
    return ValidationFailure(
        error_code=error_code,
        error_hint=error_hint(error_code),
        category=classify(error_code),
        details=details,
    )


def _function_name(node: dict[str, Any]) -> str:
    parts = node.get("funcname") or []
    names: list[str] = []
    for part in parts:
        if isinstance(part, dict) and part.get("@") == "String":
            value = part.get("sval")
            if isinstance(value, str):
                names.append(value)
        elif isinstance(part, str):
            names.append(part)
    return names[-1].lower() if names else ""


def _walk(node: Any, stats: _Stats, depth: int) -> None:
    if isinstance(node, dict):
        node_type = node.get("@")
        if node_type == "SelectStmt":
            stats.max_subquery_depth = max(stats.max_subquery_depth, depth)
            for key, value in node.items():
                if key != "@":
                    _walk(value, stats, depth + 1)
            return
        if node_type == "RangeVar":
            relname = node.get("relname")
            schema = node.get("schemaname")
            if isinstance(schema, str) and schema and schema != "agent_catalog":
                stats.forbidden_schemas.add(schema)
            if isinstance(relname, str) and relname:
                stats.tables.add(relname)
        elif node_type == "CommonTableExpr":
            cte_name = node.get("ctename")
            if isinstance(cte_name, str) and cte_name:
                stats.cte_names.add(cte_name)
        elif node_type == "FuncCall":
            function_name = _function_name(node)
            if function_name:
                stats.functions.add(function_name)
            parts = node.get("funcname") or []
            if len(parts) > 1:
                first = parts[0]
                if isinstance(first, dict) and isinstance(first.get("sval"), str):
                    stats.function_schemas.add(first["sval"])
        elif node_type == "JoinExpr":
            stats.joins += 1
        for key, value in node.items():
            if key != "@":
                _walk(value, stats, depth)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _walk(item, stats, depth)


def _integer_value(node: Any) -> int | None:
    if not isinstance(node, dict):
        return None
    value = node.get("val")
    if isinstance(value, dict) and value.get("@") == "Integer":
        return value.get("ival")
    return None


def _strip_trailing_semicolon(sql: str) -> str:
    stripped = sql.rstrip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
    return stripped


def _canonical_sql(raw_stmt: Any, fallback_sql: str) -> str:
    """Render the parsed statement back to a single canonical SQL string."""
    try:
        rendered = RawStream()(raw_stmt)
        if isinstance(rendered, str) and rendered.strip():
            return rendered.strip()
    except Exception:
        pass
    return _strip_trailing_semicolon(fallback_sql)


def validate_sql(sql: str, settings: Settings) -> ValidationOutcome:
    """Validate one model-generated SQL string and return a safe SQL to run."""
    if len(sql) > settings.sql_max_length:
        return _failure("sql_too_long", length=len(sql), max_length=settings.sql_max_length)

    try:
        statements = parse_sql(sql)
    except Exception as exc:  # pglast raises its own parser error type
        return _failure("syntax_error", parser_error=str(exc)[:200])

    if len(statements) != 1:
        return _failure("multi_statement", statement_count=len(statements))

    raw_stmt = statements[0]
    serialized = raw_stmt() if callable(raw_stmt) else raw_stmt
    stmt = serialized.get("stmt") if isinstance(serialized, dict) else None
    if not isinstance(stmt, dict) or stmt.get("@") != "SelectStmt":
        return _failure("not_select", statement_type=(stmt or {}).get("@"))

    if stmt.get("intoClause"):
        return _failure("select_into_not_allowed")
    if stmt.get("lockingClause"):
        return _failure("locking_clause_not_allowed")
    operation = stmt.get("op") or {}
    if isinstance(operation, dict) and operation.get("name") not in (None, "SETOP_NONE"):
        return _failure("set_operation_not_allowed", operation=operation.get("name"))

    stats = _Stats()
    _walk(stmt, stats, depth=0)

    real_tables = stats.tables - stats.cte_names
    forbidden_tables = sorted(table for table in real_tables if table not in ALLOWED_TABLES)
    if forbidden_tables:
        return _failure("forbidden_table", tables=forbidden_tables)

    forbidden_functions = sorted((stats.functions & FORBIDDEN_FUNCTIONS) | (stats.functions - ALLOWED_FUNCTIONS))
    if forbidden_functions:
        return _failure("forbidden_function", functions=forbidden_functions)
    if stats.forbidden_schemas or stats.function_schemas:
        return _failure("forbidden_table", schemas=sorted(stats.forbidden_schemas | stats.function_schemas))

    if stats.joins > settings.sql_max_joins:
        return _failure("too_many_joins", joins=stats.joins, max_joins=settings.sql_max_joins)
    if stats.max_subquery_depth > settings.sql_max_subquery_depth:
        return _failure(
            "subquery_too_deep",
            depth=stats.max_subquery_depth,
            max_depth=settings.sql_max_subquery_depth,
        )

    select_columns = len(stmt.get("targetList") or [])
    if select_columns > settings.sql_max_select_columns:
        return _failure(
            "too_many_select_columns",
            columns=select_columns,
            max_columns=settings.sql_max_select_columns,
        )

    limit_count = stmt.get("limitCount")
    if limit_count:
        limit_value = _integer_value(limit_count)
        if limit_value is not None and limit_value > settings.sql_max_rows:
            return _failure("limit_too_large", limit=limit_value, max_rows=settings.sql_max_rows)
        canonical = _canonical_sql(raw_stmt, sql)
    else:
        canonical = _canonical_sql(raw_stmt, sql) + f" LIMIT {settings.sql_max_rows + 1}"

    return ValidationSuccess(sql=canonical, tables=frozenset(real_tables))


__all__ = [
    "ValidationSuccess",
    "ValidationFailure",
    "ValidationOutcome",
    "validate_sql",
    "FORBIDDEN_FUNCTIONS",
    "ALLOWED_FUNCTIONS",
]
