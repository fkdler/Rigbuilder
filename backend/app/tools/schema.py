"""Expose only the stable V3 Agent View Registry; never reflect base tables."""

from copy import deepcopy
from typing import Any

from app.tools.tables import ALLOWED_TABLES, VIEW_REGISTRY


def inspect_database() -> dict[str, Any]:
    tables = []
    for name in sorted(VIEW_REGISTRY):
        definition = deepcopy(VIEW_REGISTRY[name])
        tables.append({"name": name, **definition, "foreign_keys": []})
    return {"schema": "agent_catalog", "tables": tables, "allowed_tables": sorted(ALLOWED_TABLES)}


def compact_database_schema(schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the SQL-relevant contract without verbose registry metadata.

    The full registry remains available to application code, while the Agent
    receives only view names, descriptions and column names/types. This avoids
    injecting the same multi-thousand-token schema on every inspect call.
    """
    source = schema or inspect_database()
    return {
        "schema": source.get("schema", "agent_catalog"),
        "allowed_tables": source.get("allowed_tables", []),
        "tables": [
            {
                "name": table["name"],
                "description": table.get("description", ""),
                "columns": [
                    {"name": column["name"], "type": column.get("type", "unknown")}
                    for column in table.get("columns", [])
                ],
            }
            for table in source.get("tables", [])
        ],
    }


__all__ = ["compact_database_schema", "inspect_database"]
