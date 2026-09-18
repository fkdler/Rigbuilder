"""Bounded SQL Agent state machine with native llama.cpp tool support."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any

from pydantic import ValidationError

from app.agents.context_budget import (
    breakdown_to_dict,
    compute_prompt_budget,
    prune_old_tool_exchanges,
)
from app.agents.protocol.contracts import (
    Message as CanonicalMessage,
    ToolCall as CanonicalToolCall,
    assistant_message as canonical_assistant_message,
    tool_message as canonical_tool_message,
)
from app.context.contracts import ChatMessage
from app.core.config import Settings
from app.inference.profiles import ModelProfile
from app.schemas.recommendation import Claim, Recommendation, format_validation_errors
from app.services.llm import LLMCallResult, LLMProtocolError, LLMServiceError, LLMToolCall
from app.tools import registry as tool_registry
from app.tools.contracts import INSPECT_TOOL, QUERY_TOOL, RESOLVE_TOOL, ToolCall, ToolResult
from app.tools.database import query_database, resolve_evidence
from app.tools.errors import ErrorCategory, classify, error_hint
from app.tools.schema import compact_database_schema, inspect_database
from app.agents.selection import (bound_discovery_sql, fact_selection_roles, remember_selection_rows, ready_for_selection,
                                  selection_schema, selection_brief, materialize_selection)
from app.services.timing import phase

LlmCallable = Callable[[list[ChatMessage]], Awaitable[tuple[str, str]]]
NativeLlmCallable = Callable[..., Awaitable[LLMCallResult]]

NATIVE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "inspect_database",
            "description": "Inspect the compact catalog schema only when the schema already in the system prompt is insufficient.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_database",
            "description": "Run one validated, read-only SELECT against the published agent_catalog views.",
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string", "description": "A single read-only SELECT statement."}},
                "required": ["sql"],
                "additionalProperties": False,
            },
        },
    },
]


@dataclass(frozen=True)
class ToolCallEvent:
    tool: str
    arguments: dict[str, Any]
    success: bool
    error_code: str | None
    query_id: str | None
    result_truncated: bool
    duration_ms: int
    result_summary: str | None


@dataclass(frozen=True)
class AgentLoopResult:
    status: str
    recommendation: dict[str, Any] | None
    model: str | None
    rounds_used: int
    sql_calls_used: int
    error_code: str | None
    error_message: str | None
    messages: list[tuple[str, str]] = field(default_factory=list)
    tool_calls: list[ToolCallEvent] = field(default_factory=list)
    protocol: str = "legacy"
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _ParsedToolCall:
    call: ToolCall


@dataclass(frozen=True)
class _ParsedRecommendation:
    recommendation: Recommendation


@dataclass(frozen=True)
class _InvalidReply:
    reason: str
    detail: Any = None


_ParsedReply = _ParsedToolCall | _ParsedRecommendation | _InvalidReply


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith(chr(96) * 3):
        stripped = stripped.strip(chr(96))
        stripped = stripped[4:].strip() if stripped.startswith("json") else stripped.strip()
    return stripped


def parse_model_reply(raw: str) -> _ParsedReply:
    try:
        value = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        return _InvalidReply("not_json", str(exc)[:200])
    if not isinstance(value, dict):
        return _InvalidReply("not_object", type(value).__name__)
    if value.get("type") == "tool_call":
        try:
            return _ParsedToolCall(ToolCall.model_validate(value))
        except ValidationError as exc:
            return _InvalidReply("invalid_tool_call", format_validation_errors(exc))
    if "recommendations" in value:
        try:
            return _ParsedRecommendation(Recommendation.model_validate(value))
        except ValidationError as exc:
            return _InvalidReply("invalid_recommendation", format_validation_errors(exc))
    return _InvalidReply("unrecognized_shape")


def _value_type(value: Any) -> str | None:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return None


def _claim_value_key(value: Any) -> str:
    """Comparable key for a claimed value, so equivalent spellings still match.

    Measured: this repair path (below) fills in evidence_ids when a claim's value
    identifies exactly one piece of evidence for that entity, but it compared raw strings,
    so a claim carrying 8, "8" or 8.0 could not be matched against evidence stored as
    "8.0". Agent-a submitted claims while holding nineteen known ids and still scored
    fact_support 0.00, which is what this gap looks like from the outside.
    """
    if isinstance(value, bool):
        return "bool:" + str(value).casefold()
    if isinstance(value, (int, float)):
        try:
            return "num:" + repr(float(value))
        except (OverflowError, ValueError):
            return "str:" + str(value).strip().casefold()
    text = str(value).strip().casefold()
    try:
        return "num:" + repr(float(text.replace(",", "")))
    except (TypeError, ValueError):
        return "str:" + text


def _normalize_recommendation_reply(raw: str, evidence: dict[str, dict[str, Any]]) -> str:
    """Fill structural claim metadata only when cited tool evidence proves it."""
    try:
        value = json.loads(_strip_fences(raw))
    except (json.JSONDecodeError, TypeError):
        return raw
    if not isinstance(value, dict) or not isinstance(value.get("recommendations"), list):
        return raw
    deduplicated: list[dict[str, Any]] = []
    decision_fields = [
        "gpu.vram_gib", "gpu.board_power_w", "gpu.memory_bandwidth_gb_s",
        "gpu.architecture", "gpu.memory_type", "cpu.cores_total", "cpu.threads",
        "cpu.socket", "cpu.base_power_w",
    ]
    seen_candidates: set[tuple[str, str]] = set()
    for recommendation in value["recommendations"]:
        if not isinstance(recommendation, dict):
            continue
        candidate_key = (
            str(recommendation.get("candidate_type") or ""),
            str(recommendation.get("candidate_id") or ""),
        )
        if candidate_key in seen_candidates:
            continue
        seen_candidates.add(candidate_key)
        normalized_claims: list[dict[str, Any]] = []
        for claim in recommendation.get("claims", []):
            if not isinstance(claim, dict) or claim.get("claim_type") != "fact":
                if isinstance(claim, dict):
                    try:
                        Claim.model_validate(claim)
                    except ValidationError:
                        continue
                    normalized_claims.append(claim)
                continue
            entity_id = str(claim.get("entity_id") or "")
            cited = [evidence.get(str(item)) for item in claim.get("evidence_ids", [])]
            cited = [item for item in cited if item and item.get("entity_id") == entity_id]
            if not cited:
                target = _claim_value_key(claim.get("value"))
                matches = [
                    item for item in evidence.values()
                    if item.get("entity_id") == entity_id
                    and _claim_value_key(item.get("normalized_value")) == target
                ]
                if len(matches) == 1:
                    cited = matches
                    claim["evidence_ids"] = [matches[0]["evidence_id"]]
            field_keys = {str(item["field_key"]) for item in cited if item.get("field_key")}
            if not claim.get("field_key") and len(field_keys) == 1:
                claim["field_key"] = next(iter(field_keys))
            if claim.get("value_type") == "text":
                claim["value_type"] = "string"
            inferred = _value_type(claim.get("value"))
            if not claim.get("value_type") and inferred:
                claim["value_type"] = inferred
            if claim.get("value_type") in {"number", "integer"} and isinstance(claim.get("value"), str):
                try:
                    number = float(claim["value"])
                    claim["value"] = int(number) if claim["value_type"] == "integer" else number
                except ValueError:
                    pass
            if not claim.get("unit") and cited and cited[0].get("unit"):
                claim["unit"] = cited[0]["unit"]
            try:
                Claim.model_validate(claim)
            except ValidationError:
                # Unproved claims are omitted; candidate identity is verified
                # independently against the Truth DB by the service layer.
                continue
            normalized_claims.append(claim)
        recommendation["claims"] = normalized_claims
        # Deterministically recover decision facts the model omitted after it has
        # selected a real candidate.  Every added claim is copied from the compact
        # evidence returned by query_database in this run and is revalidated below.
        candidate_id = str(recommendation.get("candidate_id") or "")
        existing_fields = {str(c.get("field_key")) for c in normalized_claims}
        for field_key in decision_fields:
            if len(normalized_claims) >= 6 or field_key in existing_fields:
                continue
            matches = [item for item in evidence.values()
                       if item.get("entity_id") == candidate_id
                       and item.get("field_key") == field_key
                       and item.get("accepted") is True]
            if not matches:
                continue
            item = sorted(matches, key=lambda x: str(x.get("evidence_id")))[0]
            claim = {
                "claim_type": "fact", "entity_id": candidate_id,
                "field_key": field_key, "value": item.get("normalized_value"),
                "value_type": _value_type(item.get("normalized_value")),
                "unit": item.get("unit"), "evidence_ids": [item.get("evidence_id")],
                "benchmark_run_ids": [], "rule_refs": [],
            }
            try:
                Claim.model_validate(claim)
            except ValidationError:
                continue
            normalized_claims.append(claim)
            existing_fields.add(field_key)
        recommendation["claims"] = normalized_claims
        deduplicated.append(recommendation)
    value["recommendations"] = deduplicated
    return json.dumps(value, ensure_ascii=False)


def _remember_evidence(tool_result: ToolResult, evidence: dict[str, dict[str, Any]]) -> None:
    """Index only the compact, server-produced evidence descriptors."""
    data = tool_result.data or {}
    observation = data.get("observation")
    if not isinstance(observation, dict):
        return
    for item in observation.get("evidence", []):
        if isinstance(item, dict) and item.get("evidence_id"):
            evidence[str(item["evidence_id"])] = item


def _remember_candidates(tool_result: ToolResult, candidates: set[str]) -> None:
    """Collect every entity id the Agent has actually seen returned by a tool.

    Measured failure this prevents: all three Agents called resolve_evidence during a
    run and then submitted a candidate_id of 00000000-0000-0000-0000-000000000000 or an
    invented UUID.  The Recommendation schema cannot tell a real UUID from a made-up
    one, so the mistake only surfaced later at Truth verification, where nothing can
    repair it any more.  Tracking the ids a tool really returned lets the loop reject
    an unprovable id at submission time and hand the model the ids it may use.
    """
    data = tool_result.data or {}
    observation = data.get("observation")
    if not isinstance(observation, dict):
        return
    columns = observation.get("columns") or []
    if "entity_id" not in columns:
        return
    index = columns.index("entity_id")
    for row in observation.get("rows") or []:
        if not isinstance(row, (list, tuple)) or index >= len(row):
            continue
        value = row[index]
        if isinstance(value, str) and len(value) == 36:
            candidates.add(value)


def _invalid_reply_hint(parsed: _InvalidReply) -> str:
    if parsed.reason == "invalid_recommendation":
        return "Fix the Recommendation schema errors and return JSON only: " + json.dumps(parsed.detail)
    if parsed.reason == "invalid_tool_call":
        return "Return exactly one valid tool_call JSON object: " + json.dumps(parsed.detail)
    return "Return exactly one JSON object: a tool_call or the final Recommendation. No markdown or prose."


async def run_tool(
    tool: str, arguments: dict[str, Any], settings: Settings, evidence_session: Any = None,
) -> tuple[ToolResult, ToolCallEvent]:
    started = time.perf_counter()
    if tool == "inspect_database":
        schema = compact_database_schema(inspect_database())
        duration_ms = int((time.perf_counter() - started) * 1000)
        return ToolResult(type="tool_result", tool=tool, success=True, data={"schema": schema}), ToolCallEvent(
            tool=tool, arguments={}, success=True, error_code=None, query_id=None,
            result_truncated=False, duration_ms=duration_ms, result_summary=f"{len(schema['tables'])} tables",
        )
    if tool == "query_database":
        sql = str(arguments.get("sql", ""))
        observation = query_database(sql, evidence_session=evidence_session)
        duration_ms = int((time.perf_counter() - started) * 1000)
        result = ToolResult(type="tool_result", tool=tool, success=observation.success,
                            data={"observation": observation.model_dump()})
        summary = (f"rows={observation.row_count} truncated={observation.truncated}"
                   if observation.success else f"error={observation.error_code}")
        return result, ToolCallEvent(
            tool=tool, arguments={"sql": sql}, success=observation.success,
            error_code=observation.error_code, query_id=observation.query_id,
            result_truncated=observation.truncated, duration_ms=duration_ms, result_summary=summary,
        )
    if tool == "resolve_evidence":
        keys = _normalize_entity_keys(arguments.get("entity_keys"))
        if not keys:
            return ToolResult(
                type="tool_result", tool=tool, success=False,
                error={"code": "invalid_tool_call",
                       "hint": "resolve_evidence requires a non-empty entity_keys list."},
            ), ToolCallEvent(
                tool=tool, arguments={"entity_keys": keys}, success=False,
                error_code="invalid_tool_call", query_id=None, result_truncated=False,
                duration_ms=int((time.perf_counter() - started) * 1000),
                result_summary="error=invalid_tool_call",
            )
        raw_fields = arguments.get("fields")
        fields = [str(item) for item in raw_fields] if isinstance(raw_fields, list) else None
        observation = resolve_evidence(
            keys,
            view=str(arguments.get("view") or "component_profile_catalog"),
            fields=fields,
            evidence_session=evidence_session,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        result = ToolResult(type="tool_result", tool=tool, success=observation.success,
                            data={"observation": observation.model_dump()})
        summary = (f"resolved={observation.row_count} evidence={len(observation.evidence or [])}"
                   if observation.success else f"error={observation.error_code}")
        return result, ToolCallEvent(
            tool=tool, arguments={"entity_keys": keys}, success=observation.success,
            error_code=observation.error_code, query_id=observation.query_id,
            result_truncated=observation.truncated, duration_ms=duration_ms, result_summary=summary,
        )
    raise ValueError(f"Unknown tool: {tool}")


def _normalize_sql(sql: str) -> str:
    """Whitespace-insensitive form used to detect a resent statement."""
    return " ".join(sql.split())


def _resend_notice(sql: str, attempts: int) -> str:
    """Tell the model that resending a statement is not a retry.

    Measured: agent-c sent the byte-identical statement six times in one run, failing the
    same way each time, and spent its entire round and SQL budget on those repeats without
    reading a single row. It then had no candidate id and invented one. The loop already
    tells the model never to resend, but a small local model does not act on a general
    instruction; pointing out that this exact statement already failed does change what it
    sends next, and it costs no database round trip.
    """
    return json.dumps({
        "type": "tool_result", "tool": "query_database", "success": False,
        "error": {
            "code": "duplicate_statement",
            "hint": (
                f"This is the {attempts}th identical statement in this run and it already "
                "failed the same way. Repeating it cannot produce a different result and "
                "wastes the remaining budget. Change the statement: read the previous "
                "error, fix the named problem, and send a genuinely different query."
            ),
        },
    }, ensure_ascii=False)


def _normalize_entity_keys(raw: Any) -> list[str]:
    """Accept the shapes a small model actually emits for entity_keys.

    Measured: an Agent ended as tool_protocol_error because it sent this argument in
    an unexpected shape, and the adapter treats unparsable arguments as fatal with no
    retry.  A list, a JSON-encoded list, a comma- or newline-separated string and a
    single key all mean the same thing here, so accept all of them rather than
    discarding a whole Agent run over punctuation.
    """
    if raw is None:
        return []
    if isinstance(raw, dict):
        # Some templates emit [{"entity_key": "..."}] instead of ["..."].
        values = [raw.get("entity_key") or raw.get("key") or ""]
        return [str(value).strip() for value in values if str(value).strip()]
    if isinstance(raw, list):
        keys: list[str] = []
        for item in raw:
            if isinstance(item, dict):
                item = item.get("entity_key") or item.get("key") or ""
            text = str(item).strip()
            if text:
                keys.append(text)
        return keys
    text = str(raw).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            decoded = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            decoded = None
        if isinstance(decoded, list):
            return _normalize_entity_keys(decoded)
    return [part.strip() for part in text.replace("\n", ",").split(",") if part.strip()]


def _usage_metrics(calls: list[LLMCallResult], tool_calls: list[ToolCallEvent] | None = None) -> dict[str, Any]:
    def total(*keys: str) -> int:
        return sum(int(call.usage.get(key, 0) or 0) for call in calls for key in keys[:1])
    timings = [call.timings for call in calls if call.timings]
    return {
        "llm_duration_ms": sum(call.duration_ms for call in calls),
        "prompt_tokens": total("prompt_tokens"),
        "completion_tokens": total("completion_tokens"),
        "total_tokens": total("total_tokens"),
        "llm_calls": len(calls),
        "prompt_eval_duration_ms": sum(float(item.get("prompt_ms", item.get("prompt_eval_duration_ms", 0))) or 0 for item in timings),
        "generation_duration_ms": sum(float(item.get("predicted_ms", item.get("generation_duration_ms", 0))) or 0 for item in timings),
        "model_load_duration_ms": sum(float(item.get("load_ms", item.get("model_load_duration_ms", 0))) or 0 for item in timings),
        "tool_duration_ms": sum(item.duration_ms for item in (tool_calls or [])),
        "llama_timings": timings,
    }


async def _run_native(
    *, messages: list[dict[str, Any]], settings: Settings, native_llm: NativeLlmCallable,
    legacy_llm: LlmCallable,
    evidence_session: Any, on_message: Callable[[str, str, int], None] | None,
    on_tool_call: Callable[[ToolCallEvent], None] | None,
    on_tool_start: Callable[[str, dict[str, Any]], None] | None,
    on_llm_call: Callable[[LLMCallResult], None] | None,
) -> AgentLoopResult:
    rounds = 0
    sql_calls = 0
    model: str | None = None
    trace_messages: list[tuple[str, str]] = []
    trace_tools: list[ToolCallEvent] = []
    calls: list[LLMCallResult] = []
    evidence_index: dict[str, dict[str, Any]] = {}
    text_tool_protocol = False

    def effective_protocol() -> str:
        return "legacy" if text_tool_protocol else "native"

    def current_metrics() -> dict[str, Any]:
        metrics = _usage_metrics(calls, trace_tools)
        metrics["native_tools_supported"] = not text_tool_protocol
        if text_tool_protocol:
            metrics["native_text_tool_fallback"] = True
        return metrics

    def record(role: str, content: str, tokens: int = 1) -> None:
        trace_messages.append((role, content))
        if on_message:
            on_message(role, content, tokens)

    async def legacy_finalize(final_messages: list[dict[str, Any]]) -> AgentLoopResult:
        """Finalize from gathered observations without exposing tools again."""
        nonlocal rounds, model
        fallback_messages = list(final_messages)
        fallback_messages.append({
            "role": "user",
            "content": "Native protocol mode is unavailable. Return Recommendation JSON only. Do not call any tool.",
        })
        fallback_started = time.perf_counter()
        attempted = 0
        for repair in range(2):
            attempted += 1
            try:
                reply, model = await legacy_llm(fallback_messages)
            except LLMServiceError as exc:
                return AgentLoopResult(
                    "failed", None, model, rounds, sql_calls,
                    ("schema_protocol_error" if isinstance(exc, LLMProtocolError)
                     else getattr(exc, "code", "router_http_error")),
                    f"Final JSON mode failed after database tools completed: {exc}",
                    trace_messages, trace_tools, "legacy", {
                        **current_metrics(), "native_fallback": True,
                        "legacy_finalize_attempts": attempted,
                    },
                )
            rounds += 1
            record("assistant", reply, max(1, len(reply) // 2 + 4))
            parsed_fallback = parse_model_reply(_normalize_recommendation_reply(reply, evidence_index))
            if isinstance(parsed_fallback, _ParsedRecommendation):
                metrics = current_metrics()
                metrics.update({
                    "native_fallback": True,
                    "legacy_finalize_duration_ms": int((time.perf_counter() - fallback_started) * 1000),
                    "legacy_finalize_attempts": attempted,
                    "llm_calls": metrics["llm_calls"] + attempted,
                })
                return AgentLoopResult(
                    "completed", parsed_fallback.recommendation.model_dump(mode="json"), model,
                    rounds, sql_calls, None, None, trace_messages, trace_tools, "legacy", metrics,
                )
            if repair == 0 and isinstance(parsed_fallback, _InvalidReply):
                fallback_messages.append({"role": "user", "content": _invalid_reply_hint(parsed_fallback)})
        return AgentLoopResult(
            "failed", None, model, rounds, sql_calls, "invalid_recommendation",
            "Legacy finalization did not return a valid Recommendation.", trace_messages, trace_tools,
            "legacy", {**current_metrics(), "native_fallback": True,
                       "legacy_finalize_attempts": attempted},
        )

    for _ in range(min(3, settings.agent_max_rounds)):
        rounds += 1
        text_encoded_call = False
        try:
            call = await native_llm(messages, tools=NATIVE_TOOLS, max_tokens=512)
        except LLMServiceError:
            if trace_tools:
                terminal_messages = [*messages, {
                    "role": "user",
                    "content": "Stop using tools and return the final Recommendation JSON from gathered evidence.",
                }]
                return await legacy_finalize(terminal_messages)
            raise
        calls.append(call)
        model = call.model
        if on_llm_call:
            on_llm_call(call)
        if not call.tool_calls:
            if call.content:
                content_reply = parse_model_reply(call.content)
                if isinstance(content_reply, _ParsedToolCall):
                    # Preserve all observations already gathered in this task.
                    # The model is marked legacy for later jobs, but this text
                    # call is safely executed once inside the bounded loop.
                    text_tool_protocol = True
                    text_encoded_call = True
                    raw = LLMToolCall(
                        id=f"text-call-{rounds}",
                        name=content_reply.call.tool,
                        arguments=json.dumps(content_reply.call.arguments.model_dump(), ensure_ascii=False),
                    )
                else:
                    record("assistant", call.content, int(call.usage.get("completion_tokens", 1) or 1))
                    messages.append({"role": "assistant", "content": call.content})
                    break
            else:
                break
        else:
            raw = call.tool_calls[0]
        tool_result: ToolResult | None = None
        invalid_tool_content: str | None = None
        try:
            arguments = json.loads(raw.arguments)
            parsed = ToolCall.model_validate({"type": "tool_call", "tool": raw.name, "arguments": arguments})
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            invalid_tool_content = json.dumps({
                "type": "tool_result", "tool": raw.name, "success": False,
                "error": {"code": "invalid_tool_call", "hint": str(exc)[:400]},
            })
            arguments = {}
            parsed = None
        if raw.name == "query_database" and sql_calls >= min(4, settings.agent_max_sql_calls):
            tool_result = ToolResult(type="tool_result", tool="query_database", success=False,
                                     error={"code": "sql_call_limit_reached", "hint": error_hint("sql_call_limit_reached")})
            parsed = None

        assistant_message = {
            "role": "assistant", "content": None if text_encoded_call else (call.content or None),
            "tool_calls": [{"id": raw.id, "type": "function",
                            "function": {"name": raw.name, "arguments": raw.arguments}}],
        }
        messages.append(assistant_message)
        record("assistant", json.dumps({"tool": raw.name, "tool_call_id": raw.id}),
               int(call.usage.get("completion_tokens", 1) or 1))

        if parsed is not None:
            if parsed.tool == "query_database":
                sql_calls += 1
            if on_tool_start:
                on_tool_start(parsed.tool, parsed.arguments.model_dump())
            tool_result, event = await run_tool(parsed.tool, parsed.arguments.model_dump(), settings, evidence_session)
            _remember_evidence(tool_result, evidence_index)
            trace_tools.append(event)
            if on_tool_call:
                on_tool_call(event)
            if not event.success and event.error_code and classify(event.error_code) is ErrorCategory.BLOCKING:
                return AgentLoopResult(
                    "failed", None, model, rounds, sql_calls, event.error_code,
                    event.result_summary or error_hint(event.error_code), trace_messages, trace_tools,
                    effective_protocol(), current_metrics(),
                )
        tool_content = tool_result.model_dump_json() if tool_result is not None else invalid_tool_content
        if tool_content is None:
            tool_content = json.dumps({"type": "tool_result", "success": False, "error": {"code": "invalid_tool_call"}})
        messages.append({"role": "tool", "tool_call_id": raw.id, "name": raw.name,
                         "content": tool_content})
        record("tool", tool_content)

    # Tools are deliberately removed for the terminal state. The schema makes
    # it impossible for a compliant server to drift back into prose/tool loops.
    final_messages = list(messages)
    final_messages.append({
        "role": "user",
        "content": "Return the final recommendation now. Use only gathered evidence and match the required JSON schema.",
    })
    try:
        final = await native_llm(
            final_messages, response_schema=_evidence_required_schema(), max_tokens=2048,
        )
    except LLMServiceError:
        if not trace_tools:
            raise
        # Native tools may work while json_schema does not. Preserve the
        # already-returned observations and allow only a final JSON repair;
        # tools are not exposed, so no successful read can be repeated.
        return await legacy_finalize(final_messages)
    calls.append(final)
    model = final.model
    rounds += 1
    if on_llm_call:
        on_llm_call(final)
    record("assistant", final.content, int(final.usage.get("completion_tokens", 1) or 1))
    parsed_final = parse_model_reply(_normalize_recommendation_reply(final.content, evidence_index))
    if isinstance(parsed_final, _ParsedRecommendation):
        return AgentLoopResult(
            "completed", parsed_final.recommendation.model_dump(mode="json"), model, rounds, sql_calls,
            None, None, trace_messages, trace_tools, effective_protocol(), current_metrics(),
        )
    if isinstance(parsed_final, _InvalidReply):
        repair_messages = [
            *final_messages,
            {"role": "assistant", "content": final.content},
            {"role": "user", "content": _invalid_reply_hint(parsed_final)},
        ]
        try:
            repaired = await native_llm(
                repair_messages, response_schema=_evidence_required_schema(), max_tokens=2048,
            )
        except LLMServiceError as exc:
            return AgentLoopResult(
                "failed", None, model, rounds, sql_calls,
                ("schema_protocol_error" if isinstance(exc, LLMProtocolError)
                 else getattr(exc, "code", "router_http_error")),
                "The model rejected schema repair mode.", trace_messages, trace_tools,
                "legacy", {
                    **_usage_metrics(calls, trace_tools),
                    "native_fallback": True,
                    "native_tools_supported": not text_tool_protocol,
                },
            )
        calls.append(repaired)
        model = repaired.model
        rounds += 1
        if on_llm_call:
            on_llm_call(repaired)
        record("assistant", repaired.content, int(repaired.usage.get("completion_tokens", 1) or 1))
        parsed_repaired = parse_model_reply(_normalize_recommendation_reply(repaired.content, evidence_index))
        if isinstance(parsed_repaired, _ParsedRecommendation):
            metrics = current_metrics()
            metrics["schema_repairs"] = 1
            return AgentLoopResult(
                "completed", parsed_repaired.recommendation.model_dump(mode="json"), model,
                rounds, sql_calls, None, None, trace_messages, trace_tools, effective_protocol(), metrics,
            )
        parsed_final = parsed_repaired
    return AgentLoopResult(
        "failed", None, model, rounds, sql_calls, "invalid_recommendation",
        _invalid_reply_hint(parsed_final) if isinstance(parsed_final, _InvalidReply) else "Invalid recommendation",
        trace_messages, trace_tools, effective_protocol(), current_metrics(),
    )


async def _run_legacy(
    *, messages: list[ChatMessage], settings: Settings, llm: LlmCallable, evidence_session: Any,
    on_message: Callable[[str, str, int], None] | None,
    on_tool_call: Callable[[ToolCallEvent], None] | None,
    on_tool_start: Callable[[str, dict[str, Any]], None] | None,
) -> AgentLoopResult:
    rounds = sql_calls = repairs = 0
    model = None
    last_code = last_message = None
    trace_messages: list[tuple[str, str]] = []
    trace_tools: list[ToolCallEvent] = []
    evidence_index: dict[str, dict[str, Any]] = {}
    started = time.perf_counter()

    def record(role: str, content: str, tokens: int) -> None:
        trace_messages.append((role, content))
        if on_message:
            on_message(role, content, tokens)

    while rounds < min(4, settings.agent_max_rounds):
        rounds += 1
        reply, model = await llm(messages)
        record("assistant", reply, max(1, len(reply) // 2 + 4))
        parsed = parse_model_reply(_normalize_recommendation_reply(reply, evidence_index))
        if isinstance(parsed, _ParsedRecommendation):
            return AgentLoopResult(
                "completed", parsed.recommendation.model_dump(mode="json"), model, rounds, sql_calls,
                None, None, trace_messages, trace_tools, "legacy",
                {"llm_duration_ms": int((time.perf_counter() - started) * 1000), "llm_calls": rounds,
                 "tool_duration_ms": sum(item.duration_ms for item in trace_tools)},
            )
        if isinstance(parsed, _InvalidReply):
            feedback = _invalid_reply_hint(parsed)
            if repairs < 1:
                messages.append({"role": "user", "content": feedback})
                record("tool", feedback, max(1, len(feedback) // 2 + 4))
                repairs += 1
            last_code, last_message = "invalid_reply", feedback
            continue
        tool = parsed.call.tool
        arguments = parsed.call.arguments.model_dump()
        if tool == "query_database" and sql_calls >= min(4, settings.agent_max_sql_calls):
            feedback = error_hint("sql_call_limit_reached")
            result = ToolResult(type="tool_result", tool=tool, success=False,
                                error={"code": "sql_call_limit_reached", "hint": feedback})
            messages.append({"role": "tool", "content": result.model_dump_json()})
            record("tool", result.model_dump_json(), 1)
            last_code, last_message = "sql_call_limit_reached", feedback
            continue
        if on_tool_start:
            on_tool_start(tool, arguments)
        tool_result, event = await run_tool(tool, arguments, settings, evidence_session)
        _remember_evidence(tool_result, evidence_index)
        if tool == "query_database":
            sql_calls += 1
        trace_tools.append(event)
        if on_tool_call:
            on_tool_call(event)
        messages.append({"role": "tool", "content": tool_result.model_dump_json()})
        record("tool", tool_result.model_dump_json(), 1)
        if not event.success and event.error_code and classify(event.error_code) is ErrorCategory.BLOCKING:
            return AgentLoopResult(
                "failed", None, model, rounds, sql_calls, event.error_code,
                event.result_summary or error_hint(event.error_code), trace_messages, trace_tools, "legacy",
            )
    return AgentLoopResult(
        "failed", None, model, rounds, sql_calls, last_code or "round_limit_reached",
        last_message or "Agent round limit reached without a valid recommendation.",
        trace_messages, trace_tools, "legacy",
        {"llm_duration_ms": int((time.perf_counter() - started) * 1000), "llm_calls": rounds,
         "tool_duration_ms": sum(item.duration_ms for item in trace_tools)},
    )


async def run_agent_loop(
    *, messages: list[ChatMessage], settings: Settings, llm: LlmCallable,
    native_llm: NativeLlmCallable | None = None, native_allowed: bool = True,
    evidence_session: Any = None,
    on_message: Callable[[str, str, int], None] | None = None,
    on_tool_call: Callable[[ToolCallEvent], None] | None = None,
    on_tool_start: Callable[[str, dict[str, Any]], None] | None = None,
    on_llm_call: Callable[[LLMCallResult], None] | None = None,
) -> AgentLoopResult:
    """Use native tools first; only protocol incompatibility triggers legacy."""
    if native_llm is not None and native_allowed:
        try:
            return await _run_native(
                messages=list(messages), settings=settings, native_llm=native_llm,
                legacy_llm=llm,
                evidence_session=evidence_session, on_message=on_message,
                on_tool_call=on_tool_call, on_tool_start=on_tool_start, on_llm_call=on_llm_call,
            )
        except LLMProtocolError:
            # The legacy pass starts from the original prompt, so a successful
            # native SQL call is never silently repeated after partial progress.
            # Protocol errors are expected during the initial capability probe.
            pass
    return await _run_legacy(
        messages=messages, settings=settings, llm=llm, evidence_session=evidence_session,
        on_message=on_message, on_tool_call=on_tool_call, on_tool_start=on_tool_start,
    )


# ---------------------------------------------------------------------------
# V3.3 protocol path: canonical messages + submit_recommendation terminal tool.
# The legacy native/schema path above is deliberately preserved until the new
# path passes automated and real-hardware smoke tests.
# ---------------------------------------------------------------------------


def _dict_messages_to_canonical(messages: list[ChatMessage]) -> list[CanonicalMessage]:
    converted: list[CanonicalMessage] = []
    for message in messages:
        content = message.get("content")
        converted.append(
            CanonicalMessage(
                role=str(message.get("role", "user")),
                content=content if content is not None else "",
            )
        )
    return converted


def _synthetic_llm_call(turn) -> LLMCallResult:
    """Expose an AssistantTurn to existing LLM-usage event callbacks."""
    return LLMCallResult(
        content=turn.content,
        tool_calls=[],
        finish_reason=turn.finish_reason,
        model=turn.model,
        usage=turn.usage,
        timings=turn.timings,
        duration_ms=turn.duration_ms,
        call_mode=turn.protocol,
    )


def _protocol_usage_metrics(
    turns: list[Any], tool_calls: list[ToolCallEvent] | None = None
) -> dict[str, Any]:
    """Usage metrics over canonical AssistantTurns (same keys as _usage_metrics)."""
    def total(*keys: str) -> int:
        return sum(int(turn.usage.get(keys[0], 0) or 0) for turn in turns)
    timings = [turn.timings for turn in turns if turn.timings]
    return {
        "llm_duration_ms": sum(int(turn.duration_ms or 0) for turn in turns),
        "prompt_tokens": total("prompt_tokens"),
        "completion_tokens": total("completion_tokens"),
        "total_tokens": total("total_tokens"),
        "llm_calls": len(turns),
        "prompt_eval_duration_ms": sum(
            float(item.get("prompt_ms", item.get("prompt_eval_duration_ms", 0))) or 0 for item in timings
        ),
        "generation_duration_ms": sum(
            float(item.get("predicted_ms", item.get("generation_duration_ms", 0))) or 0 for item in timings
        ),
        "model_load_duration_ms": sum(
            float(item.get("load_ms", item.get("model_load_duration_ms", 0))) or 0 for item in timings
        ),
        "tool_duration_ms": sum(item.duration_ms for item in (tool_calls or [])),
        "llama_timings": timings,
        "native_tools_supported": True,
        "protocol_path": True,
    }


_UNCLAIMED_PATTERNS: list[tuple[str, str]] = [
    (r"(\d+(?:[.,]\d+)?)\s*(gb|gib)\b", "gpu.vram_gib"),
    (r"(\d+(?:[.,]\d+)?)\s*(cny|usd|rmb|元|¥|\$)", "price"),
    (r"(\d+(?:[.,]\d+)?)\s*(w|watt|瓦)\b", "gpu.board_power_w"),
    (r"(\d+(?:[.,]\d+)?)\s*(%|percent)(?=\s|$|[,.，。])", "percentage"),
    (r"(\d+(?:[.,]\d+)?)\s*(fps|tokens?\s*(?:/s|per\s+second)|tok(?:en)?s?/s|ms)\b", "benchmark"),
]


def _numbers_equal(left: Any, right: Any) -> bool:
    try:
        return abs(float(str(left).replace(",", "")) - float(str(right).replace(",", ""))) < 1e-9
    except (TypeError, ValueError):
        return False


def _normalized_unit(value: Any) -> str:
    return "" if value is None else "".join(str(value).casefold().split())


def _unit_matches(key: str, text_unit: str, claim: Claim) -> bool:
    """Require the Claim unit to describe the exact unit stated to the user."""
    actual = _normalized_unit(claim.unit)
    stated = _normalized_unit(text_unit)
    if key == "price":
        expected = "usd" if stated in {"usd", "$"} else "cny"
        return actual == expected
    if key == "gpu.board_power_w":
        return actual in {"w", "watt"}
    if key == "percentage":
        return actual in {"%", "percent", "percentage"}
    if key == "benchmark":
        aliases = {
            "fps": {"fps"},
            "ms": {"ms", "millisecond", "milliseconds"},
            "tokens/s": {"token/s", "tokens/s", "tok/s", "t/s", "tokenspersecond"},
        }
        family = "tokens/s" if "tok" in stated else stated
        return actual in aliases.get(family, {family})
    return actual == stated


def _claim_coverage_issues(recommendation: Recommendation) -> list[dict[str, Any]]:
    """Find explicit measurable facts not backed by a same-candidate Claim."""
    import re

    issues: list[dict[str, Any]] = []
    for candidate_index, item in enumerate(recommendation.recommendations):
        candidate_claims = list(item.claims)
        claim_map: dict[str, list[Any]] = {}
        for claim in candidate_claims:
            key = "price" if claim.claim_type == "price" else claim.field_key
            if key:
                claim_map.setdefault(key, []).append(claim)
        text = " ".join(item.reasons + item.risks)
        for pattern, key in _UNCLAIMED_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                raw_value = match.group(1).replace(",", "")
                text_unit = match.group(2)
                if key in {"percentage", "benchmark"}:
                    candidates = [
                        claim for claim in candidate_claims
                        if claim.claim_type in {"measurement", "derived"}
                    ]
                else:
                    candidates = claim_map.get(key, [])
                matching = [
                    claim for claim in candidates
                    if _numbers_equal(claim.value, raw_value) and _unit_matches(key, text_unit, claim)
                ]
                if matching:
                    continue
                issues.append({
                    "loc": ["recommendations", candidate_index, "reasons_risks"],
                    "type": "claim_coverage",
                    "message": (
                        f"The stated {key} value {match.group(0)!r} must have a same-candidate "
                        "Claim with the identical value and appropriate unit."
                    ),
                })
    return issues


def _unclaimed_fact_count(recommendation: Recommendation) -> int:
    """Heuristic: count reason/risk mentions of verifiable numbers without a claim.

    Non-blocking signal; the metric surfaces in AgentRun.metrics so operators can
    track claim coverage without changing the verification verdict.
    """
    return len(_claim_coverage_issues(recommendation))


def _evidence_required_schema() -> dict[str, Any]:
    """Live finalization schema that cannot emit evidence-free candidates."""
    schema = Recommendation.model_json_schema()
    try:
        schema["$defs"]["RecommendationItem"]["properties"]["claims"]["minItems"] = 1
    except (KeyError, TypeError):
        pass
    return schema


def _validate_submission(
    arguments: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
    allowed_candidate_ids: set[str] | None = None,
) -> tuple[Recommendation | None, list[dict[str, Any]] | None]:
    """Strictly validate a submit_recommendation payload after provable repairs."""
    try:
        raw_text = json.dumps(arguments, ensure_ascii=False)
    except (TypeError, ValueError):
        return None, [{"loc": ["$"], "type": "invalid_recommendation",
                       "message": "Recommendation payload is not JSON-serializable."}]
    try:
        value = json.loads(_normalize_recommendation_reply(raw_text, evidence))
    except json.JSONDecodeError as exc:
        return None, [{"loc": ["$"], "type": "not_json", "message": str(exc)[:200]}]
    if not isinstance(value, dict):
        return None, [{"loc": ["$"], "type": "not_object", "message": type(value).__name__}]
    try:
        recommendation = Recommendation.model_validate(value)
    except ValidationError as exc:
        return None, format_validation_errors(exc)
    unprovable = _unprovable_candidates(recommendation, allowed_candidate_ids)
    if unprovable:
        return None, unprovable
    coverage_issues = _claim_coverage_issues(recommendation)
    if coverage_issues:
        return None, coverage_issues
    return recommendation, None


def _unprovable_candidates(
    recommendation: Recommendation, allowed_candidate_ids: set[str] | None,
) -> list[dict[str, Any]]:
    """Report candidate ids that no tool of this run ever returned.

    Deliberately inert when the Agent never obtained an id, so a run that legitimately
    recommends nothing cannot be blocked by this check: the gate only fires once the
    model has been handed real ids and then submits something else.
    """
    if not allowed_candidate_ids:
        return []
    issues: list[dict[str, Any]] = []
    listing = sorted(allowed_candidate_ids)[:8]
    for index, item in enumerate(recommendation.recommendations):
        if str(item.candidate_id) in allowed_candidate_ids:
            continue
        issues.append({
            "loc": ["recommendations", index, "candidate_id"],
            "type": "unprovable_candidate_id",
            "message": (
                f"candidate_id {item.candidate_id} was never returned by a tool in this "
                "run. Truth verification would reject it as candidate_not_found. Use one "
                "of the ids a tool returned, or call resolve_evidence with the entity_key "
                "values and copy candidate_id from its rows. Known ids: "
                + ", ".join(listing)
            ),
        })
    return issues


async def _prompt_budget(
    profile: ModelProfile,
    messages: list[Any],
    tools: list[Any],
    settings: Settings,
) -> Any:
    """Use the discovered slot window and server-rendered prompt count.

    If rendering/counting is unavailable, use the shared conservative estimate.
    """
    from app.agents.protocol.adapter import LlamaCppOpenAIAdapter
    from app.inference.servers import count_prompt_tokens
    from app.inference.context_window import resolve_context_window

    profile = await resolve_context_window(profile, settings)

    exact: int | None = None
    try:
        wire = LlamaCppOpenAIAdapter.messages_to_openai(messages)
        exact = await count_prompt_tokens(profile, wire, settings,
            wire_tools=LlamaCppOpenAIAdapter.tools_to_openai(tools) if tools else None)
    except Exception:  # noqa: BLE001 - a counting failure must never fail the run
        exact = None
    return compute_prompt_budget(profile, messages, tools, exact_prompt_tokens=exact)


async def run_agent_loop_protocol(
    *,
    handle: Any,
    profile: ModelProfile,
    messages: list[ChatMessage],
    settings: Settings,
    evidence_session: Any = None,
    on_message: Callable[[str, str, int], None] | None = None,
    on_tool_call: Callable[[ToolCallEvent], None] | None = None,
    on_tool_start: Callable[[str, dict[str, Any]], None] | None = None,
    on_llm_call: Callable[[LLMCallResult], None] | None = None,
    max_terminal_retries: int = 2,
) -> AgentLoopResult:
    """Run one Agent entirely in canonical Tool Calling protocol.

    The model gathers facts through query_database and terminates with
    submit_recommendation.  Schema validation failures are returned as ordinary
    ToolResults so the same model repairs its submission without re-running SQL
    or restarting the Agent.  No Tool-mode -> JSON-Schema-mode switch happens.
    """
    started = time.perf_counter()
    rounds = 0
    sql_calls = 0
    terminal_attempts = 0
    model: str | None = None
    trace_messages: list[tuple[str, str]] = []
    trace_tools: list[ToolCallEvent] = []
    turns: list[Any] = []
    evidence_index: dict[str, dict[str, Any]] = {}
    # Every entity id a tool has actually returned in this run. Empty means the model
    # was never handed a real id, and the candidate gate stays inert in that case.
    known_candidate_ids: set[str] = set()
    selection_rows: dict[str, dict[str, Any]] = {}
    selection_roles = fact_selection_roles(messages) if not profile.terminal_tool_supported else set()
    canonical_messages = _dict_messages_to_canonical(messages)
    tools = (
        tool_registry.agent_full_tools()
        if profile.terminal_tool_supported
        else tool_registry.agent_database_tools()
    )

    def record(role: str, content: str, tokens: int = 1) -> None:
        trace_messages.append((role, content))
        if on_message:
            on_message(role, content, tokens)

    def current_metrics() -> dict[str, Any]:
        metrics = _protocol_usage_metrics(turns, trace_tools)
        metrics["native_tools_supported"] = True
        # Non-zero means the Agent only stayed inside its context window because the
        # oldest tool results were dropped, so the trace says so explicitly.
        metrics["context_pruned_messages"] = pruned_messages
        # Non-zero means the model resent statements it had already run, and the loop
        # refused to execute them a second time.
        metrics["duplicate_statements_rejected"] = _resend_rejections
        # Visible evidence of whether the model was ever handed a usable candidate id.
        metrics["known_candidate_ids"] = len(known_candidate_ids)
        metrics["selection_candidates"] = len(selection_rows)
        return metrics

    def finished_result(
        recommendation: Recommendation, terminal_attempts_used: int,
    ) -> AgentLoopResult:
        from app.agents.selection import preserve_requested_pool
        recommendation = preserve_requested_pool(recommendation, selection_rows, newest_request)
        metrics = current_metrics()
        metrics["terminal_tool"] = True
        metrics["submit_attempts"] = terminal_attempts_used
        metrics["unclaimed_reasons_count"] = _unclaimed_fact_count(recommendation)
        return AgentLoopResult(
            status="completed",
            recommendation=recommendation.model_dump(mode="json"),
            model=model,
            rounds_used=rounds,
            sql_calls_used=sql_calls,
            error_code=None,
            error_message=None,
            messages=trace_messages,
            tool_calls=trace_tools,
            protocol="native",
            metrics={
                **metrics,
                "agent_duration_ms": int((time.perf_counter() - started) * 1000),
                "native_fallback": False,
            },
        )

    def failed_result(code: str, message: str) -> AgentLoopResult:
        return AgentLoopResult(
            status="failed",
            recommendation=None,
            model=model,
            rounds_used=rounds,
            sql_calls_used=sql_calls,
            error_code=code,
            error_message=message,
            messages=trace_messages,
            tool_calls=trace_tools,
            protocol="native",
            metrics=current_metrics(),
        )

    max_rounds = min(profile.max_tool_rounds, settings.agent_max_rounds)
    # Measured: agents submit in the SAME turn as their last query, and a real
    # submission is 600-2 200 characters (agent-c produced 1 330 and then 2 111 in
    # one turn and was cut off mid-JSON at exactly 2 048 tokens). A tool-only round
    # finishes in 7-324 tokens, so the wide cap costs nothing when the model is just
    # querying and is the difference between a parseable submission and a dead run.
    # It stays well inside the 16 384-token context that reserves this budget.
    round_max_tokens = 512 if selection_roles else 8_192
    # The terminal finalize writes the whole Recommendation rather than one tool call, so it
    # is the one generation that genuinely needs headroom. It used round_max_tokens, which is
    # four times prompt_reserved_completion_tokens (2 048): the preflight only guarantees the
    # gap it reserved, so asking for more than that is asking the server for room it was never
    # promised. Measured: two of three Agents on one question produced 5 046 and 5 301
    # character submissions that stopped mid-string, while the one that stayed near 3 379
    # characters parsed and passed verification. A prompt budget check on that run showed
    # 10 072 prompt tokens and a 6 312-token gap, so 4 096 finishes inside the window.
    finalize_max_tokens = 4_096
    pruned_messages = 0
    # Whitespace-normalised statements this run has already executed, with how many times
    # the model has sent each. Measured: six identical resends in a single run.
    attempted_statements: dict[str, int] = {}
    _resend_rejections = 0
    # Backend gaming discovery uses the ordinary read-only tool and evidence path.
    from app.services.hardware_intent import discovery_queries, allows_candidate, sku_requests
    newest_request = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    from app.services.routing import requested_component_roles
    planned_queries = discovery_queries(newest_request, requested_component_roles(newest_request))
    for sql in planned_queries[:min(profile.max_sql_calls, settings.agent_max_sql_calls)]:
        if on_tool_start:
            on_tool_start("query_database", {"sql": sql})
        tool_result, event = await run_tool("query_database", {"sql": sql}, settings, evidence_session)
        sql_calls += 1
        attempted_statements[_normalize_sql(sql)] = 1
        event = replace(event, arguments={"sql": sql})
        trace_tools.append(event)
        if on_tool_call:
            on_tool_call(event)
        _remember_evidence(tool_result, evidence_index)
        _remember_candidates(tool_result, known_candidate_ids)
        remember_selection_rows(tool_result, selection_rows)
        content = tool_result.model_dump_json()
        call = CanonicalToolCall(id=f"discovery_{sql_calls}", name="query_database", arguments={"sql": sql})
        canonical_messages.append(canonical_assistant_message(None, [call]))
        canonical_messages.append(canonical_tool_message(call.id, call.name, content))
        record("tool", content, 1)
        if not event.success:
            return failed_result("catalogue_discovery_failed", "The catalogue discovery query failed; no unverified substitution is allowed.")
    if planned_queries:
        selection_rows = {key: row for key, row in selection_rows.items()
                          if allows_candidate(newest_request, row["role"], row["name"])}
        if requested_component_roles(newest_request) == {'model'} and not ready_for_selection(selection_rows, {'model'}):
            return failed_result('model_catalogue_gap', 'No evidence-bound model matches the requested catalogue filters; do not substitute hardware or waive the constraints.')
        for role, spec in sku_requests(newest_request).items():
            if role in selection_roles and spec["required"] and not any(row["role"] == role for row in selection_rows.values()):
                return failed_result("requested_sku_unavailable", "No evidence-bound candidate matches the requested SKU in the bounded catalogue query.")
    while rounds < max_rounds:
        if planned_queries and ready_for_selection(selection_rows, selection_roles):
            break
        # Context preflight: never let the prompt overflow n_ctx and wait for a
        # Router HTTP 400. Measured: without pruning, a three-Agent fusion reached
        # 17 527 and 18 353 real prompt tokens against a 16 384 window purely from
        # accumulated tool results, so the round was rejected and the Agent never got
        # to submit. Drop the oldest tool exchanges first and only give up if that is
        # still not enough.
        with phase("context_preflight", model=profile.model_id, round=rounds + 1):
            budget = await _prompt_budget(profile, canonical_messages, tools, settings)
        if budget.exceeds:
            pruned = prune_old_tool_exchanges(
                canonical_messages,
                # getattr: the loop is also driven with lightweight settings stand-ins
                # in tests, and the pruning budget must not require the full Settings.
                keep_tool_rounds=getattr(settings, "context_keep_tool_rounds", 2),
                max_messages=getattr(settings, "context_prune_max_messages", 40),
            )
            if pruned is not canonical_messages and len(pruned) < len(canonical_messages):
                pruned_messages += len(canonical_messages) - len(pruned)
                canonical_messages = pruned
                budget = await _prompt_budget(profile, canonical_messages, tools, settings)
        if budget.exceeds:
            return failed_result(
                "context_budget_exceeded",
                "Conversation exceeds the model context budget before the next round, "
                "even after dropping the oldest tool results: "
                + json.dumps(breakdown_to_dict(budget)),
            )
        rounds += 1
        try:
            turn = await handle.turn(canonical_messages, tools=tools, max_tokens=budget.completion_limit(round_max_tokens))
        except LLMServiceError as exc:
            code = getattr(exc, "code", "router_http_error")
            message = f"The inference server failed during a protocol tool round: {exc}"
            if isinstance(exc, LLMProtocolError):
                code = "tool_protocol_error"
                message = str(exc)
            return failed_result(code, message)
        turns.append(turn)
        model = turn.model or model
        if on_llm_call:
            on_llm_call(_synthetic_llm_call(turn))

        if not turn.tool_calls:
            content = (turn.content or "").strip()
            if not content:
                # Nothing useful generated; stop instead of burning the budget.
                return failed_result("empty_reply", "The model returned no content or tool call.")
            record("assistant", content, int(turn.usage.get("completion_tokens", 1) or 1))
            # A text-only reply may already be a valid Recommendation JSON (some
            # models ignore tools). Otherwise push the model back to the tool.
            parsed = parse_model_reply(_normalize_recommendation_reply(content, evidence_index))
            if isinstance(parsed, _ParsedRecommendation):
                return finished_result(parsed.recommendation, terminal_attempts)
            canonical_messages.append(canonical_assistant_message(content))
            # Never ask for a tool the model does not have: when the terminal tool
            # is disabled the tools list holds database tools only, and instructing
            # the model to "call submit_recommendation" made it repeat prose until
            # the round limit (measured: the same JSON body four rounds in a row).
            if selection_roles:
                covered = {row["role"] for row in selection_rows.values() if row["facts"]}
                nudge = ("Continue query_database discovery for roles: " + ", ".join(sorted(selection_roles - covered))
                         + ". SELECT entity_id, entity_key, name and relevant facts, LIMIT 5. "
                         "Do not write prose or final JSON; the backend requests selection after gathering evidence.")
            elif profile.terminal_tool_supported:
                nudge = ("Return the final answer by calling submit_recommendation only. "
                         "Do not reply with prose.")
            else:
                nudge = ("Return the final Recommendation JSON object now, as your whole "
                         "reply, matching the schema in the system prompt. Do not call a "
                         "tool and do not reply with prose.")
            canonical_messages.append(CanonicalMessage(role="user", content=nudge))
            continue

        # Only one tool call is expected per turn (parallel_tool_calls disabled),
        # but a compliant provider may still return several. Process sequentially.
        submit_finished: tuple[Recommendation, int] | None = None
        for raw in turn.tool_calls:
            if not isinstance(raw, CanonicalToolCall):
                # Canonical conversion failures are surfaced by the adapter; skip
                # anything that is not a canonical tool call.
                continue
            arguments = raw.arguments if isinstance(raw.arguments, dict) else {}
            if raw.name == "submit_recommendation":
                terminal_attempts += 1
                recommendation, details = _validate_submission(
                    arguments, evidence_index, known_candidate_ids
                )
                if recommendation is not None:
                    record("assistant", json.dumps({"tool": raw.name, "tool_call_id": raw.id},
                                                   ensure_ascii=False), 1)
                    submit_finished = (recommendation, terminal_attempts)
                    break
                content = json.dumps({
                    "type": "tool_result",
                    "tool": raw.name,
                    "success": False,
                    "error": {
                        "code": "recommendation_schema_validation_failed",
                        "details": details or [],
                        # Measured: agents that had already been handed real ids still
                        # submitted an invented one, and a rejection that only says
                        # "invalid" gives them nothing to repair with. Every id listed
                        # here was returned by a tool earlier in this same run.
                        "usable_candidate_ids": sorted(known_candidate_ids)[:8],
                    },
                }, ensure_ascii=False)
                canonical_messages.append(canonical_assistant_message(None, [raw]))
                canonical_messages.append(canonical_tool_message(raw.id, raw.name, content))
                record("assistant", json.dumps({"tool": raw.name, "tool_call_id": raw.id}), 1)
                record("tool", content, 1)
                trace_tools.append(ToolCallEvent(
                    tool=raw.name, arguments=arguments, success=False,
                    error_code="recommendation_schema_validation_failed", query_id=None,
                    result_truncated=False, duration_ms=0,
                    result_summary="recommendation_schema_validation_failed",
                ))
                if on_tool_call:
                    on_tool_call(trace_tools[-1])
                if terminal_attempts > max_terminal_retries:
                    return failed_result(
                        "schema_validation_failed",
                        "submit_recommendation failed schema validation after repeated attempts.",
                    )
                continue

            # The non-terminal tools come from the contracts module so a new tool
            # cannot be registered here and forgotten in the ToolCall literal, which
            # is exactly how resolve_evidence first broke every Agent run.
            if raw.name not in {INSPECT_TOOL, QUERY_TOOL, RESOLVE_TOOL}:
                content = json.dumps({
                    "type": "tool_result", "tool": raw.name, "success": False,
                    "error": {"code": "unknown_tool", "hint": f"Unknown tool {raw.name}."},
                }, ensure_ascii=False)
                canonical_messages.append(canonical_assistant_message(None, [raw]))
                canonical_messages.append(canonical_tool_message(raw.id, raw.name, content))
                record("tool", content, 1)
                continue

            # ---- inspect_database / query_database / resolve_evidence ------
            tool_arguments: dict[str, Any]
            if raw.name == "inspect_database":
                tool_arguments = {}
            elif raw.name == "resolve_evidence":
                keys = _normalize_entity_keys(arguments.get("entity_keys"))
                if not keys:
                    content = json.dumps({
                        "type": "tool_result", "tool": raw.name, "success": False,
                        "error": {"code": "invalid_tool_call",
                                  "hint": "resolve_evidence requires a non-empty entity_keys list."},
                    }, ensure_ascii=False)
                    canonical_messages.append(canonical_assistant_message(None, [raw]))
                    canonical_messages.append(canonical_tool_message(raw.id, raw.name, content))
                    record("tool", content, 1)
                    continue
                # Resolving identifiers is a read of the Truth DB too, so it shares
                # the same per-run budget as a SQL call without being one.
                if sql_calls >= min(settings.agent_max_sql_calls, profile.max_sql_calls):
                    hint = error_hint("sql_call_limit_reached")
                    content = json.dumps({
                        "type": "tool_result", "tool": raw.name, "success": False,
                        "error": {"code": "sql_call_limit_reached", "hint": hint},
                    }, ensure_ascii=False)
                    canonical_messages.append(canonical_assistant_message(None, [raw]))
                    canonical_messages.append(canonical_tool_message(raw.id, raw.name, content))
                    record("tool", content, 1)
                    continue
                sql_calls += 1
                raw_fields = arguments.get("fields")
                tool_arguments = {"entity_keys": keys}
                if isinstance(raw_fields, list) and raw_fields:
                    tool_arguments["fields"] = [str(item) for item in raw_fields]
                if arguments.get("view"):
                    tool_arguments["view"] = str(arguments["view"])
            else:
                sql = arguments.get("sql")
                if not isinstance(sql, str) or not sql.strip():
                    content = json.dumps({
                        "type": "tool_result", "tool": raw.name, "success": False,
                        "error": {"code": "invalid_tool_call", "hint": "query_database requires a non-empty sql argument."},
                    }, ensure_ascii=False)
                    canonical_messages.append(canonical_assistant_message(None, [raw]))
                    canonical_messages.append(canonical_tool_message(raw.id, raw.name, content))
                    record("tool", content, 1)
                    continue
                if sql_calls >= min(settings.agent_max_sql_calls, profile.max_sql_calls):
                    hint = error_hint("sql_call_limit_reached")
                    content = json.dumps({
                        "type": "tool_result", "tool": raw.name, "success": False,
                        "error": {"code": "sql_call_limit_reached", "hint": hint},
                    }, ensure_ascii=False)
                    canonical_messages.append(canonical_assistant_message(None, [raw]))
                    canonical_messages.append(canonical_tool_message(raw.id, raw.name, content))
                    record("tool", content, 1)
                    continue
                if selection_roles:
                    sql = bound_discovery_sql(sql)
                normalized = _normalize_sql(sql)
                resend_count = attempted_statements.get(normalized, 0)
                if resend_count:
                    # Never spend a database round trip, or a round of the budget, on a
                    # statement that has already run unchanged.
                    attempted_statements[normalized] = resend_count + 1
                    _resend_rejections += 1
                    content = _resend_notice(sql, resend_count + 1)
                    canonical_messages.append(canonical_assistant_message(None, [raw]))
                    canonical_messages.append(canonical_tool_message(raw.id, raw.name, content))
                    record("tool", content, 1)
                    trace_tools.append(ToolCallEvent(
                        tool=raw.name, arguments={"sql": sql}, success=False,
                        error_code="duplicate_statement", query_id=None,
                        result_truncated=False, duration_ms=0,
                        result_summary="error=duplicate_statement",
                    ))
                    if on_tool_call:
                        on_tool_call(trace_tools[-1])
                    continue
                attempted_statements[normalized] = 1
                sql_calls += 1
                tool_arguments = {"sql": sql}

            if on_tool_start:
                on_tool_start(raw.name, tool_arguments)
            tool_result, event = await run_tool(
                raw.name, tool_arguments, settings, evidence_session
            )
            _remember_evidence(tool_result, evidence_index)
            _remember_candidates(tool_result, known_candidate_ids)
            if selection_roles:
                remember_selection_rows(tool_result, selection_rows)
            trace_tools.append(event)
            if on_tool_call:
                on_tool_call(event)
            if not event.success and event.error_code and classify(event.error_code) is ErrorCategory.BLOCKING:
                return failed_result(event.error_code, event.result_summary or error_hint(event.error_code))
            content = tool_result.model_dump_json()
            canonical_messages.append(canonical_assistant_message(None, [raw]))
            canonical_messages.append(canonical_tool_message(raw.id, raw.name, content))
            record("assistant", json.dumps({"tool": raw.name, "tool_call_id": raw.id}), 1)
            record("tool", content, 1)

        if submit_finished is not None:
            return finished_result(submit_finished[0], submit_finished[1])
        if selection_roles and ready_for_selection(selection_rows, selection_roles):
            # Enough alternatives for each requested role: rank what we have.
            # Price/benchmark/confirmed-constraint requests never enter this path.
            break
        if not profile.terminal_tool_supported and _resend_rejections >= 2:
            break

    if not profile.terminal_tool_supported:
        if selection_roles and ready_for_selection(selection_rows, selection_roles):
            # Do not make a small model retype UUID-bound facts and dozens of null
            # Claim properties. It still owns selection and reasons; the backend
            # supplies only the facts returned by this run and verifies them later.
            selection_messages = [CanonicalMessage(role="system", content=(
                "Your product knowledge may be outdated. Select the requested products from supplied catalogue rows only. Return JSON matching the schema. "
                "Never invent specifications, release dates, comparisons or performance. Use evidence IDs in the supplied facts; missing facts stay unknown. "
                "Use short Chinese reasons connecting actual facts to the user's need. No prices or FPS claims. "
                "For a CPU+GPU request select at least one of EACH role. For a GPU request never select a CPU. "
                "For a gaming build, obey the supplied balance policy and performance indices; an entry-tier CPU "
                "cannot be paired with a high-tier GPU. If no policy-valid pair is present, do not emit a complete build. "
                "No budget/resolution was confirmed unless stated: do not assume the most expensive flagship is best, "
                "unless the user explicitly asked for 顶尖/顶级/旗舰/极致 performance. "
                "Do not copy claims or names; the backend binds them from the selected row."
            ))]
            selection_messages.extend(_dict_messages_to_canonical([m for m in messages if m["role"] != "system"][-4:]))
            from app.services.execution_intent import execution_intent
            plan = execution_intent.get()
            if plan:
                selection_messages[0] = CanonicalMessage(role="system", content=selection_messages[0].content + "\n" + plan.instruction())
            selection_messages.append(CanonicalMessage(role="user", content=(
                "Required roles: " + ", ".join(sorted(selection_roles)) + "\nCandidates: "
                + selection_brief(selection_rows, selection_roles))))
            with phase("selection_preflight", model=profile.model_id):
                budget = await _prompt_budget(profile, selection_messages, [], settings)
            if budget.exceeds:
                return failed_result("context_budget_exceeded", "Selection exceeds the model context budget.")
            try:
                turn = await handle.turn(selection_messages, response_schema=selection_schema(selection_rows, selection_roles),
                                         max_tokens=budget.completion_limit(900))
                turns.append(turn)
                model = turn.model or model
                if on_llm_call:
                    on_llm_call(_synthetic_llm_call(turn))
                record("assistant", turn.content or "", int(turn.usage.get("completion_tokens", 1) or 1))
                newest_request = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
                recommendation = materialize_selection(turn.content, selection_rows, selection_roles, newest_request)
                result = finished_result(recommendation, terminal_attempts)
                return replace(result, metrics={**result.metrics, "compact_selection": True, "terminal_tool": False})
            except LLMServiceError as exc:
                return failed_result(getattr(exc, "code", "llm_error"), "Inference service failed during selection.")
            except (ValueError, KeyError, TypeError):
                return failed_result("invalid_selection", "The catalogue selection could not be validated.")
        # Last-chance finalize. Without a terminal tool the model never submits by
        # itself, so the round limit used to end every run as round_limit_reached
        # even when the evidence for a full answer was already collected. This is
        # the same schema-constrained finish the legacy path has always had, and it
        # can neither re-run a read nor invent evidence.
        if known_candidate_ids:
            id_guidance = (
                " candidate_id MUST be one of these ids returned by a tool in this run "
                "(copy the exact string, do not retype it): "
                + ", ".join(sorted(known_candidate_ids)[:8])
            )
        else:
            # Measured: agent-c produced no usable id in 9 of 9 evaluation runs because
            # its queries selected entity_key but never entity_id, so the tool had no
            # UUID to report; it then invented one. This runs after the round budget is
            # spent, so it cannot ask for another query - it can only refuse to invent.
            id_guidance = (
                " No candidate id was returned by any tool in this run, so no candidate "
                "can be verified. Return {\"recommendations\": []} and list what is missing "
                "under insufficient_information. Do not invent an id, and do not name a "
                "product that no query returned: this Release holds 20 NVIDIA cards "
                "(RTX 30, 40 and 50 series) in gpu_catalog and nothing else."
            )
        final_messages = [*canonical_messages, CanonicalMessage(
            role="user",
            content=("Return the final Recommendation JSON object now, using only the rows "
                     "and evidence already returned above. Every recommended candidate must "
                     "include at least one Claim copied from a same-row evidence binding. "
                     "Match the schema exactly."
                     + id_guidance),
        )]
        budget = await _prompt_budget(profile, final_messages, [], settings)
        if budget.exceeds:
            return failed_result('context_budget_exceeded', 'Final answer exceeds the model context budget.')
        try:
            final_turn = await handle.turn(
                final_messages,
                response_schema=_evidence_required_schema(),
                max_tokens=budget.completion_limit(finalize_max_tokens),
            )
        except LLMServiceError as exc:
            return failed_result(
                getattr(exc, "code", "router_http_error"),
                f"Terminal schema finalize failed: {exc}",
            )
        turns.append(final_turn)
        model = final_turn.model or model
        if on_llm_call:
            on_llm_call(_synthetic_llm_call(final_turn))
        content = (final_turn.content or "").strip()
        if content:
            record("assistant", content, int(final_turn.usage.get("completion_tokens", 1) or 1))
            parsed_final = parse_model_reply(
                _normalize_recommendation_reply(content, evidence_index)
            )
            if isinstance(parsed_final, _ParsedRecommendation):
                result = finished_result(parsed_final.recommendation, terminal_attempts)
                metrics = {**result.metrics, "terminal_finalize": True}
                return replace(result, metrics=metrics)
            # A schema-constrained reply that still fails the domain rules keeps its
            # own diagnosis instead of being reported as a spent round budget.
            failure = failed_result(
                "invalid_recommendation",
                "The terminal schema finalize returned a payload that failed the "
                "Recommendation contract.",
            )
            metrics = {**failure.metrics, "terminal_finalize": True}
            return replace(failure, metrics=metrics)

    return failed_result(
        "round_limit_reached",
        "The Agent reached its tool round limit without a submitted Recommendation.",
    )


__all__ = [
    "AgentLoopResult", "LlmCallable", "NATIVE_TOOLS", "NativeLlmCallable", "ToolCallEvent",
    "parse_model_reply", "run_agent_loop", "run_agent_loop_protocol", "run_tool",
]
