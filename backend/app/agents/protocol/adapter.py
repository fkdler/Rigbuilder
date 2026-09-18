"""llama.cpp OpenAI-compatible Protocol Adapter (Plan V3.3).

The Adapter is the only place that knows the OpenAI ``tool_calls`` payload
shape.  It converts canonical protocol objects into request messages and turns
the gateway response back into an :class:`AssistantTurn`.  Malformed provider
output is normalized into ``LLMProtocolError`` before it can reach the Agent
loop.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from app.agents.protocol.contracts import AssistantTurn, Message, ToolCall, ToolDefinition
from app.inference.profiles import ModelProfile
from app.services.llm import LLMCallResult, LLMProtocolError, complete_chat

ProtocolMode = Literal["native_tools", "chat", "schema"]


class LlamaCppOpenAIAdapter:
    """Stateless conversions for the llama.cpp OpenAI-compatible endpoint."""

    @staticmethod
    def tools_to_openai(definitions: list[ToolDefinition]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": definition.name,
                    "description": definition.description,
                    "parameters": definition.parameters,
                },
            }
            for definition in definitions
        ]

    @staticmethod
    def messages_to_openai(messages: list[Message]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages:
            entry: dict[str, Any] = {"role": message.role, "content": message.content}
            if message.role == "assistant" and message.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, ensure_ascii=False),
                        },
                    }
                    for call in message.tool_calls
                ]
            if message.role == "tool":
                entry["tool_call_id"] = message.tool_call_id
                entry["name"] = message.name
                entry["content"] = message.content or ""
            converted.append(entry)
        return converted

    @staticmethod
    def to_assistant_turn(result: LLMCallResult, protocol: ProtocolMode = "native_tools") -> AssistantTurn:
        parsed_calls: list[ToolCall] = []
        for raw in result.tool_calls:
            raw_arguments = (raw.arguments or "").strip()
            if not raw_arguments:
                raise LLMProtocolError(
                    f"The inference server returned empty tool arguments for {raw.name}."
                )
            try:
                arguments = json.loads(raw_arguments)
            except (json.JSONDecodeError, TypeError) as exc:
                raise LLMProtocolError(
                    f"The inference server returned unparsable tool arguments for {raw.name}."
                ) from exc
            if not isinstance(arguments, dict):
                raise LLMProtocolError(
                    f"The inference server returned non-object tool arguments for {raw.name}."
                )
            parsed_calls.append(ToolCall(id=raw.id, name=raw.name, arguments=arguments))
        return AssistantTurn(
            content=result.content,
            tool_calls=parsed_calls,
            finish_reason=result.finish_reason or "stop",
            usage=result.usage,
            timings=result.timings,
            model=result.model,
            duration_ms=result.duration_ms,
            protocol=protocol,
        )


openai_adapter = LlamaCppOpenAIAdapter()


class ModelHandle:
    """One Agent's inference handle bound to a profile endpoint.

    ``chat`` is injectable so tests can fake a complete model turn without an
    HTTP server.  Defaults to the shared :func:`complete_chat` gateway.
    """

    def __init__(
        self,
        profile: ModelProfile,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        self.profile = profile
        self.timeout_seconds = timeout_seconds

    async def turn(
        self,
        messages: list[Message],
        *,
        tools: list[ToolDefinition] | None = None,
        response_schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> AssistantTurn:
        mode: ProtocolMode
        if tools is not None:
            mode = "native_tools"
        elif response_schema is not None:
            mode = "schema"
        else:
            mode = "chat"
        result = await complete_chat(
            LlamaCppOpenAIAdapter.messages_to_openai(messages),
            model_id=self.profile.model_id,
            base_url=self.profile.endpoint_url,
            tools=LlamaCppOpenAIAdapter.tools_to_openai(tools) if tools is not None else None,
            response_schema=response_schema,
            max_tokens=max_tokens,
            timeout_seconds=self.timeout_seconds,
        )
        return LlamaCppOpenAIAdapter.to_assistant_turn(result, protocol=mode)


__all__ = ["LlamaCppOpenAIAdapter", "ModelHandle", "openai_adapter"]
