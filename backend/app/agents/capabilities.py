"""Capability smoke tests and in-process state cache (Plan V3.3 §6).

Before a real user request, each configured profile may run four probe turns:

Test 1  the model produces a parseable ToolCall for a simple mock tool;
Test 2  after the backend injects a ToolResult the model continues the round;
Test 3  two consecutive tool calls keep tool_call_id history intact;
Test 4  a terminal tool's arguments parse into the target schema.

A profile whose tool protocol fails is marked TOOL_PROTOCOL_UNSUPPORTED and must
not enter a real fusion.  Without weights (or when an endpoint is unreachable)
the state stays SKIPPED and the app never blocks on it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.agents.protocol.adapter import ModelHandle
from app.agents.protocol.contracts import (
    AssistantTurn,
    Message,
    ToolCall,
    ToolDefinition,
    assistant_message,
    tool_message,
)
from app.core.config import Settings, get_settings
from app.inference.profiles import CapabilityState, ModelProfile
from app.inference.servers import EndpointProbe, probe_endpoint
from app.services.llm import LLMServiceError

_ECHO_TOOL = ToolDefinition(
    name="echo",
    description="Return the value argument unchanged.",
    parameters={
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    },
)

_TERMINAL_PROBE = ToolDefinition(
    name="submit_probe",
    description="Terminal probe tool; its arguments are the probe schema.",
    parameters={
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    },
)


class CapabilityStore:
    """Process-local capability cache keyed by profile id."""

    def __init__(self) -> None:
        self._state: dict[str, tuple[CapabilityState, dict[str, Any]]] = {}

    def record(self, profile_id: str, state: CapabilityState, detail: dict[str, Any]) -> None:
        self._state[profile_id] = (state, detail)

    def get(self, profile_id: str) -> tuple[CapabilityState, dict[str, Any]] | None:
        return self._state.get(profile_id)

    def state(self, profile_id: str) -> CapabilityState:
        entry = self._state.get(profile_id)
        return entry[0] if entry else "unverified"


capability_store = CapabilityStore()


async def _call_echo(handle: ModelHandle, instruction: str) -> AssistantTurn:
    return await handle.turn(
        [Message(role="user", content=instruction)],
        tools=[_ECHO_TOOL],
        max_tokens=160,
    )


def _first_call(turn: AssistantTurn) -> ToolCall | None:
    return turn.tool_calls[0] if turn.tool_calls else None


async def _test_1_single_tool_call(handle: ModelHandle) -> tuple[bool, str]:
    turn = await _call_echo(handle, 'Call echo with {"value": "ping"}.')
    call = _first_call(turn)
    if call is None or call.name != "echo":
        return False, "model did not emit an echo tool call"
    if not isinstance(call.arguments, dict) or call.arguments.get("value") != "ping":
        return False, "echo arguments did not parse into the target schema"
    return True, "ok"


async def _test_2_tool_result_continues(handle: ModelHandle) -> tuple[bool, str]:
    first = await _call_echo(handle, 'Call echo with {"value": "step1"}.')
    call = _first_call(first)
    if call is None:
        return False, "no first tool call for multi-turn check"
    continued = await handle.turn(
        [
            Message(role="user", content='Call echo with {"value": "step1"}'),
            assistant_message(None, [call]),
            tool_message(call.id, "echo", json.dumps({"echoed": "step1"})),
            Message(role="user", content="Continue; answer with any next action."),
        ],
        tools=[_ECHO_TOOL],
        max_tokens=160,
    )
    if not continued.content and not continued.tool_calls:
        return False, "model did not continue after the tool result was injected"
    return True, "ok"


async def _test_3_two_consecutive_tool_calls(handle: ModelHandle) -> tuple[bool, str]:
    seen = 0
    messages: list[Message] = [Message(role="user", content='Call echo twice: first with {"value": "a"}, then with {"value": "b"} in separate turns.')]
    for _ in range(2):
        turn = await handle.turn(messages, tools=[_ECHO_TOOL], max_tokens=160)
        call = _first_call(turn)
        if call is None or call.name != "echo":
            break
        seen += 1
        messages.append(assistant_message(None, [call]))
        messages.append(tool_message(call.id, call.name, json.dumps({"echoed": call.arguments.get("value")})))
        messages.append(Message(role="user", content="Continue the next tool call now."))
    return (seen >= 2, "ok") if seen >= 2 else (False, f"only {seen} consecutive tool call(s)")

async def _test_4_terminal_arguments_parse(handle: ModelHandle) -> tuple[bool, str]:
    turn = await handle.turn(
        [Message(role="user", content='Finish by calling submit_probe with {"value": "done"}')],
        tools=[_TERMINAL_PROBE],
        max_tokens=160,
    )
    call = _first_call(turn)
    if call is None or call.name != "submit_probe":
        return False, "terminal tool was not called"
    if not isinstance(call.arguments, dict) or call.arguments.get("value") != "done":
        return False, "terminal arguments did not parse into the target schema"
    return True, "ok"


async def smoke_profile(
    profile: ModelProfile,
    settings: Settings | None = None,
    *,
    record: bool = True,
) -> tuple[CapabilityState, dict[str, Any]]:
    """Run Tests 1-4 against one endpoint and store/return its capability state."""
    settings = settings or get_settings()
    probe: EndpointProbe = await probe_endpoint(profile, settings, timeout_seconds=20.0)
    if not probe.reachable:
        detail = {"reason": "endpoint_unavailable", "error": probe.error}
        if record:
            capability_store.record(profile.id, "skipped", detail)
        return "skipped", detail

    handle = ModelHandle(profile)
    tests: list[tuple[str, bool, str]] = []
    try:
        tests.append(("test_1_single_tool_call", *(await _test_1_single_tool_call(handle))))
        tests.append(("test_2_tool_result_continues", *(await _test_2_tool_result_continues(handle))))
        tests.append(("test_3_two_consecutive_tool_calls", *(await _test_3_two_consecutive_tool_calls(handle))))
        tests.append(("test_4_terminal_arguments_parse", *(await _test_4_terminal_arguments_parse(handle))))
    except (LLMServiceError, ValueError, TypeError) as exc:
        detail = {"reason": "smoke_exception", "error": str(exc), "tests": [
            {"name": name, "passed": ok, "note": note} for name, ok, note in tests
        ]}
        if record:
            capability_store.record(profile.id, "tool_protocol_unsupported", detail)
        return "tool_protocol_unsupported", detail

    all_passed = all(ok for _, ok, _ in tests)
    detail = {
        "endpoint": profile.endpoint_url,
        "tests": [{"name": name, "passed": ok, "note": note} for name, ok, note in tests],
    }
    state: CapabilityState = "ready" if all_passed else "tool_protocol_unsupported"
    if record:
        capability_store.record(profile.id, state, detail)
    return state, detail


async def startup_capability_smoke(settings: Settings | None = None) -> dict[str, tuple[CapabilityState, dict[str, Any]]]:
    """Smoke every configured profile when CAPABILITY_SMOKE_ON_STARTUP is enabled."""
    settings = settings or get_settings()
    if not settings.capability_smoke_on_startup:
        return {}
    from app.inference.profiles import build_profiles

    profiles = build_profiles(settings)
    results = await asyncio.gather(*(smoke_profile(profile, settings) for profile in profiles))
    return {profile.id: result for profile, result in zip(profiles, results)}


__all__ = [
    "CapabilityStore",
    "capability_store",
    "smoke_profile",
    "startup_capability_smoke",
]
