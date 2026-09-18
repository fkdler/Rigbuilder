"""Agent tools package with lazy exports.

Importing a lightweight registry or offline checker must not instantiate
Settings or a database engine.
"""

from importlib import import_module

_EXPORTS = {
    "InspectDatabaseArguments": ("app.tools.contracts", "InspectDatabaseArguments"),
    "Observation": ("app.tools.contracts", "Observation"),
    "QueryDatabaseArguments": ("app.tools.contracts", "QueryDatabaseArguments"),
    "ToolCall": ("app.tools.contracts", "ToolCall"),
    "ToolResult": ("app.tools.contracts", "ToolResult"),
    "query_database": ("app.tools.database", "query_database"),
    "inspect_database": ("app.tools.schema", "inspect_database"),
    "ALLOWED_TABLES": ("app.tools.tables", "ALLOWED_TABLES"),
    "validate_sql": ("app.tools.validator", "validate_sql"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attribute = _EXPORTS[name]
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
