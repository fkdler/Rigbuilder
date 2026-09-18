"""Tool protocol contracts for the controlled SQL Agent (Plan V2.2 §3.2).

The model only emits a ToolCall and receives a ToolResult. A query result is
carried as an Observation whose truncation flag must never be ambiguous.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import Settings
from app.schemas.decision_context import DecisionContext

# Same source as Settings.sql_max_length: the Pydantic bound is resolved once at
# import time so the contract and the validator pre-check cannot drift apart.
SQL_MAX_LENGTH = int(Settings.model_fields["sql_max_length"].default)

# Canonical tool names (Plan V3.3): the last one is the terminal tool.
INSPECT_TOOL = "inspect_database"
QUERY_TOOL = "query_database"
RESOLVE_TOOL = "resolve_evidence"
TERMINAL_TOOL = "submit_recommendation"
TOOL_NAMES: frozenset[str] = frozenset({INSPECT_TOOL, QUERY_TOOL, RESOLVE_TOOL, TERMINAL_TOOL})
ToolName = Literal["inspect_database", "query_database", "resolve_evidence", "submit_recommendation"]


class InspectDatabaseArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResolveEvidenceArguments(BaseModel):
    """entity_key values to turn into real candidate ids and evidence ids."""

    model_config = ConfigDict(extra="forbid")

    entity_keys: list[str] = Field(min_length=1)
    view: str | None = None
    fields: list[str] | None = None


class QueryDatabaseArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1, max_length=SQL_MAX_LENGTH)


class SubmitRecommendationArguments(BaseModel):
    """Raw Recommendation payload accepted from the model.

    Strict validation is intentionally deferred to the Agent loop so invalid
    submissions surface as actionable ToolResult details instead of failing the
    whole parse.  ``extra="allow"`` keeps the raw shape for that repair path.
    """

    model_config = ConfigDict(extra="allow")


class ToolCall(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    tool: ToolName
    arguments: (
        InspectDatabaseArguments | QueryDatabaseArguments | ResolveEvidenceArguments
        | SubmitRecommendationArguments
    )

    @model_validator(mode="after")
    def arguments_match_tool(self) -> "ToolCall":
        expected: type = {
            "inspect_database": InspectDatabaseArguments,
            "query_database": QueryDatabaseArguments,
            "resolve_evidence": ResolveEvidenceArguments,
            "submit_recommendation": SubmitRecommendationArguments,
        }[self.tool]
        # The loose Submit model may otherwise swallow an invalid DB-tool payload
        # during union resolution; tool/arguments kind must stay consistent.
        if not isinstance(self.arguments, expected):
            raise ValueError(f"arguments do not match the {self.tool} tool")
        return self


class Observation(BaseModel):
    """Structured query result for model context and audit logs."""

    success: bool
    decision_context: DecisionContext | None = None
    columns: list[str] | None = None
    rows: list[list[Any]] | None = None
    row_count: int = 0
    truncated: bool = False
    # V3.3: distinguishes the row cap from the ToolResult token/char budget so
    # the model knows which self-correction is expected.
    truncated_reason: str | None = None
    reduction_hint: str | None = None
    query_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    # Exact entity/field association for constructing verifiable fact claims.
    # This remains model-facing only; public execution events expose counts.
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    # V3.4 structured per-row evidence bindings. Each item explicitly carries
    # the entity identity and its field -> value/evidence mapping.
    row_bindings: list[dict[str, Any]] | None = None
    error_code: str | None = None
    error_hint: str | None = None
    # Database/driver message for a failed execution. Model-facing only: public
    # execution events keep reporting error_hint, so the UI never shows raw
    # database internals, while the Agent can still correct its own SQL.
    error_detail: str | None = None
    # A successful query that matched nothing. Model-facing like error_detail, and
    # present only on a zero-row result: the loop cannot otherwise tell "the data is
    # absent" from "this condition is too narrow", and measured runs show the model
    # relaxing the one condition it can see is numeric while the categorical filter
    # that actually empties the result is never questioned.
    zero_row_hint: str | None = None


class ToolResult(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool: ToolName
    success: bool
    data: dict[str, Any] | None = None
    error: dict[str, str] | None = None


__all__ = [
    "INSPECT_TOOL",
    "InspectDatabaseArguments",
    "QUERY_TOOL",
    "QueryDatabaseArguments",
    "RESOLVE_TOOL",
    "ResolveEvidenceArguments",
    "SQL_MAX_LENGTH",
    "SubmitRecommendationArguments",
    "TERMINAL_TOOL",
    "TOOL_NAMES",
    "ToolCall",
    "ToolName",
    "Observation",
    "ToolResult",
]
