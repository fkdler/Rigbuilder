"""Single source of canonical tool definitions (Plan V3.3).

The protocol Agent loop selects tools from here instead of hand-writing
provider payloads.  Legacy JSON/text paths keep their own NATIVE_TOOLS until the
new terminal-tool path is accepted on real hardware.
"""

from __future__ import annotations

from app.agents.protocol.contracts import ToolDefinition
from app.schemas.recommendation import Recommendation

_QUERY_PARAMETERS = {
    "type": "object",
    "properties": {
        "sql": {
            "type": "string",
            "description": "A single read-only SELECT statement.",
        }
    },
    "required": ["sql"],
    "additionalProperties": False,
}


def inspect_database_tool() -> ToolDefinition:
    return ToolDefinition(
        name="inspect_database",
        description="Inspect the compact catalog schema only when the schema already in the system prompt is insufficient.",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
    )


def query_database_tool() -> ToolDefinition:
    return ToolDefinition(
        name="query_database",
        description="Run one validated, read-only SELECT against the published agent_catalog views.",
        parameters=_QUERY_PARAMETERS,
    )


def resolve_evidence_tool() -> ToolDefinition:
    """Map entity_key values to the real UUID and the accepted evidence ids.

    Small local models cannot reconstruct a 36-character UUID, and an invented
    candidate id fails Truth verification outright.  This tool hands back the
    identifiers the Release already holds, so a fact claim only ever copies them.
    """
    return ToolDefinition(
        name="resolve_evidence",
        description=(
            "Resolve entity_key values read from a catalogue view into the entity's real "
            "candidate_id (UUID) plus one entry per field that has accepted evidence: "
            "field_key, value, unit, evidence_id. Copy candidate_id and evidence_id from "
            "this result; never construct them from memory."
        ),
        parameters={
            "type": "object",
            "properties": {
                "entity_keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "entity_key values exactly as returned by query_database.",
                },
                "view": {
                    "type": "string",
                    "description": (
                        "The agent_catalog view those keys came from. The default, "
                        "component_profile_catalog, lists every hardware identity, so a CPU "
                        "key and a GPU key resolve correctly in one call without naming a view."
                    ),
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional field_key filter, e.g. ['gpu.vram_gib','gpu.board_power_w']. "
                        "Ask only for the fields you will cite, to keep the result small."
                    ),
                },
            },
            "required": ["entity_keys"],
            "additionalProperties": False,
        },
    )


def submit_recommendation_tool() -> ToolDefinition:
    """Terminal tool whose arguments *are* the Recommendation schema.

    It performs no external operation; the backend validates and, on failure,
    returns a normal ToolResult so the same model can repair its submission.
    """
    return ToolDefinition(
        name="submit_recommendation",
        description=(
            "Submit the final Recommendation JSON matching the given schema. "
            "Call this only after gathering evidence with query_database. "
            "Use candidate_id values returned by the database and cite real "
            "evidence_ids from observation.evidence; never invent facts."
        ),
        parameters=Recommendation.model_json_schema(),
    )


def database_tools() -> list[ToolDefinition]:
    return [inspect_database_tool(), query_database_tool(), resolve_evidence_tool()]


def full_tools() -> list[ToolDefinition]:
    """Tools exposed on the protocol path when terminal tool is supported."""
    return [*database_tools(), submit_recommendation_tool()]


def agent_database_tools() -> list[ToolDefinition]:
    """Lean tool set for the bounded Agent loop.

    The schema is embedded in its system prompt, and query_database already
    returns entity ids plus same-row evidence bindings. Keeping inspect/resolve
    callable through the registry preserves diagnostics and compatibility, while
    removing them from model choice prevents redundant tool rounds on small LLMs.
    """
    return [query_database_tool()]


def agent_full_tools() -> list[ToolDefinition]:
    return [query_database_tool(), submit_recommendation_tool()]


def tools_by_name() -> dict[str, ToolDefinition]:
    return {tool.name: tool for tool in full_tools()}


__all__ = [
    "agent_database_tools",
    "agent_full_tools",
    "database_tools",
    "full_tools",
    "inspect_database_tool",
    "query_database_tool",
    "resolve_evidence_tool",
    "submit_recommendation_tool",
    "tools_by_name",
]
