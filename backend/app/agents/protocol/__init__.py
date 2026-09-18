"""Canonical Tool Protocol: contracts + llama.cpp OpenAI-compatible adapter."""

from app.agents.protocol.adapter import LlamaCppOpenAIAdapter, ModelHandle, openai_adapter
from app.agents.protocol.contracts import (
    AssistantTurn,
    Message,
    ToolCall,
    ToolDefinition,
    ToolResult,
    assistant_message,
    tool_message,
)

__all__ = [
    "AssistantTurn",
    "LlamaCppOpenAIAdapter",
    "Message",
    "ModelHandle",
    "ToolCall",
    "ToolDefinition",
    "ToolResult",
    "assistant_message",
    "openai_adapter",
    "tool_message",
]
