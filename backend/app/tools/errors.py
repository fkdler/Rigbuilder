"""Structured SQL Validator error codes, hints and classifications.

The Agent loop uses the classification to decide between guiding the model to
self-correct (CORRECTABLE) and terminating the current direction (BLOCKING).
"""

from enum import StrEnum


class ErrorCategory(StrEnum):
    CORRECTABLE = "correctable"
    BLOCKING = "blocking"


ERROR_HINTS: dict[str, str] = {
    "sql_too_long": "SQL exceeds the allowed length; shorten the query or query fewer tables at once.",
    "syntax_error": "SQL could not be parsed by PostgreSQL; fix the syntax and retry.",
    "multi_statement": "Only one statement is allowed per query.",
    "not_select": "Only a single SELECT (optionally WITH ... SELECT) is allowed.",
    "select_into_not_allowed": "SELECT ... INTO is a write operation and is not allowed.",
    "locking_clause_not_allowed": "Row-locking clauses (FOR UPDATE/FOR SHARE) are not allowed.",
    "forbidden_table": "Query references a table outside the allowed Truth DB whitelist.",
    "forbidden_function": "Query calls a forbidden function.",
    "set_operation_not_allowed": "UNION/INTERSECT/EXCEPT set operations are not supported; query one table set per call.",
    "too_many_joins": "Query has too many JOINs; simplify it.",
    "subquery_too_deep": "Query has too many nested subqueries; simplify it.",
    "too_many_select_columns": "Query selects too many columns; list only needed columns.",
    "limit_too_large": "LIMIT exceeds the allowed maximum rows.",
    "execution_error": "The query could not be executed (timeout or database error).",
    "database_not_configured": "The read-only database account is not configured.",
    "sql_call_limit_reached": "The per-run SQL call budget is exhausted; answer from available information.",
    "tool_arguments_invalid": "Tool arguments are invalid.",
    # V3.3 budget/terminal codes.
    "sql_result_too_large": "The query result exceeded the context budget; re-run a narrower query with fewer rows or columns.",
    "recommendation_schema_validation_failed": "The Recommendation payload failed schema validation; fix the reported fields and call submit_recommendation again.",
    "context_budget_exceeded": "The conversation exceeds the model context budget; end the run or narrow earlier tool results.",
}

# Errors the model is allowed to fix and retry. Anything else is treated as a
# deliberate policy violation and terminates the current run direction.
CORRECTABLE_CODES = frozenset({
    "syntax_error",
    "forbidden_table",
    "set_operation_not_allowed",
    "too_many_joins",
    "subquery_too_deep",
    "too_many_select_columns",
    "limit_too_large",
    "execution_error",
})


def classify(error_code: str) -> ErrorCategory:
    """Return the policy category for a validator error code."""
    if error_code in CORRECTABLE_CODES:
        return ErrorCategory.CORRECTABLE
    return ErrorCategory.BLOCKING


def error_hint(error_code: str) -> str:
    """Return the model-facing hint for an error code, with a safe fallback."""
    return ERROR_HINTS.get(error_code, "The database tool rejected the request.")
