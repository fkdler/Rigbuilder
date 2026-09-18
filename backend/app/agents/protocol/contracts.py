"""Canonical Tool Protocol contracts (Plan V3.3).

AgentRunner code only ever exchanges these objects.  Provider-specific shapes
(such as OpenAI ``tool_calls`` arrays, Gemma tool tokens, Qwen XML function
syntax or Mistral function-call blocks) belong to the Adapter layer and must
never leak into the Agent loop or prompts.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]
FinishReason = Literal["stop", "tool_calls", "length", "content_filter", "error"]


class ToolDefinition(BaseModel):
    """Canonical function definition exposed to a model."""

    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    """A model-produced function invocation, independent of provider syntax."""

    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """A tool execution outcome addressed to one ToolCall id."""

    tool_call_id: str
    name: str
    success: bool
    content: str | None = None
    data: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    truncated: bool = False


class Message(BaseModel):
    """Canonical chat message used by the protocol Agent loop."""

    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class AssistantTurn(BaseModel):
    """One normalized model response."""

    content: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: FinishReason | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    timings: dict[str, Any] = Field(default_factory=dict)
    model: str = ""
    duration_ms: int = 0
    protocol: Literal["native_tools", "chat", "schema"] = "native_tools"


def tool_message(tool_call_id: str, name: str, content: str) -> Message:
    return Message(role="tool", tool_call_id=tool_call_id, name=name, content=content)


def assistant_message(content: str | None, tool_calls: list[ToolCall] | None = None) -> Message:
    return Message(role="assistant", content=content, tool_calls=tool_calls)


__all__ = [
    "AssistantTurn",
    "Message",
    "Role",
    "ToolCall",
    "ToolDefinition",
    "ToolResult",
    "assistant_message",
    "tool_message",
]
