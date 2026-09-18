"""Context preflight and prompt budget accounting (Plan V3.3 §4).

The Agent must never discover `prompt tokens > n_ctx` through a Router HTTP 400.
Before every model round we estimate each prompt component and require

    total_prompt_tokens + reserved_completion_tokens <= context_size

Fallback estimates share the UTF-8 estimator used for conversation history.
Rendered server token counts take precedence; neither is generated-output usage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.agents.protocol.contracts import Message, ToolDefinition
from app.inference.profiles import ModelProfile
from app.inference.token_estimation import estimate_tokens


@dataclass(frozen=True)
class PromptBudget:
    context_size: int
    reserved_completion_tokens: int
    system_tokens: int = 0
    history_tokens: int = 0
    tool_schema_tokens: int = 0
    tool_result_tokens: int = 0
    total_prompt_tokens: int = 0
    # The server's own count for this prompt, when it could be obtained. Measured: the
    # estimate is 37% low on a UUID-heavy history, so this figure - not the estimate - is what
    # decides whether the prompt fits.
    exact_prompt_tokens: int | None = None

    @property
    def prompt_capacity(self) -> int:
        """Maximum prompt tokens allowed before generation starts."""
        return self.context_size - self.reserved_completion_tokens

    @property
    def effective_prompt_tokens(self) -> int:
        """What the model will actually receive, measured if possible."""
        return (
            self.exact_prompt_tokens
            if self.exact_prompt_tokens is not None
            else self.total_prompt_tokens
        )

    @property
    def exceeds(self) -> bool:
        return self.effective_prompt_tokens > self.prompt_capacity

    @property
    def remaining_prompt_tokens(self) -> int:
        return max(0, self.prompt_capacity - self.effective_prompt_tokens)

    def completion_limit(self, requested: int) -> int:
        return max(0, min(requested, self.context_size - self.effective_prompt_tokens))


def breakdown_to_dict(budget: PromptBudget) -> dict[str, Any]:
    return {
        "context_size": budget.context_size,
        "reserved_completion_tokens": budget.reserved_completion_tokens,
        "system_tokens": budget.system_tokens,
        "history_tokens": budget.history_tokens,
        "tool_schema_tokens": budget.tool_schema_tokens,
        "tool_result_tokens": budget.tool_result_tokens,
        "total_prompt_tokens": budget.total_prompt_tokens,
        "exact_prompt_tokens": budget.exact_prompt_tokens,
        "effective_prompt_tokens": budget.effective_prompt_tokens,
        "prompt_capacity": budget.prompt_capacity,
    }


def _message_text(message: Message) -> int:
    content = message.content or ""
    tokens = estimate_tokens(content)
    if message.role == "assistant" and message.tool_calls:
        for call in message.tool_calls:
            tokens += estimate_tokens(call.name) + estimate_tokens(json.dumps(call.arguments, ensure_ascii=False))
    return tokens


def _messages_tokens(messages: list[Message]) -> tuple[int, int]:
    """Return (history_tokens, tool_result_tokens) for canonical messages."""
    history = 0
    tool_results = 0
    for message in messages:
        if message.role == "tool":
            tool_results += _message_text(message)
        else:
            history += _message_text(message)
    return history, tool_results


def _tools_schema_tokens(tools: list[ToolDefinition] | None) -> int:
    if not tools:
        return 0
    total = 0
    for tool in tools:
        total += estimate_tokens(
            tool.name + tool.description + json.dumps(tool.parameters, ensure_ascii=False)
        )
    return total


def compute_prompt_budget(
    profile: ModelProfile,
    messages: list[Message],
    tools: list[ToolDefinition] | None = None,
    *,
    system_prompt: str | None = None,
    exact_prompt_tokens: int | None = None,
) -> PromptBudget:
    """Break down an estimated prompt into system/history/tool-schema/result.

    ``exact_prompt_tokens`` is the server's own count for the same conversation, when the
    caller could obtain it. The breakdown stays estimated either way - it exists to explain
    where the budget went - but the fit decision uses the measured figure when present.
    """
    history_tokens, tool_result_tokens = _messages_tokens(messages)
    system_tokens = estimate_tokens(system_prompt) if system_prompt else 0
    tool_schema_tokens = _tools_schema_tokens(tools)
    total = system_tokens + history_tokens + tool_schema_tokens + tool_result_tokens
    return PromptBudget(
        context_size=profile.context_size,
        reserved_completion_tokens=profile.reserved_completion_tokens,
        system_tokens=system_tokens,
        history_tokens=history_tokens,
        tool_schema_tokens=tool_schema_tokens,
        tool_result_tokens=tool_result_tokens,
        total_prompt_tokens=total,
        exact_prompt_tokens=exact_prompt_tokens,
    )


__all__ = [
    "PromptBudget",
    "breakdown_to_dict",
    "compute_prompt_budget",
    "estimate_tokens",
    "prune_old_tool_exchanges",
]


# Tools whose results must survive pruning: a resolve_evidence answer carries the
# only real candidate_id and evidence_id the Agent will cite, so dropping it would
# force the model to invent identifiers again.
PROTECTED_TOOLS: frozenset[str] = frozenset({"resolve_evidence"})


def _tool_exchanges(messages: list[Message]) -> list[tuple[int, int, bool, str]]:
    """Locate (assistant_index, tool_index, protected, tool_call_id) pairs.

    The loop appends every result as an assistant tool-call message followed by its
    tool message, so a pair is only dropped together: removing one without the other
    leaves a dangling tool_call_id that llama.cpp rejects.
    """
    exchanges: list[tuple[int, int, bool, str]] = []
    for index, message in enumerate(messages):
        if message.role != "tool" or not message.tool_call_id:
            continue
        name = ""
        assistant_index = index - 1
        if assistant_index >= 0:
            previous = messages[assistant_index]
            if previous.role == "assistant" and previous.tool_calls:
                for call in previous.tool_calls:
                    if call.id == message.tool_call_id:
                        name = call.name
                        break
            else:
                assistant_index = -1
        exchanges.append((assistant_index, index, name in PROTECTED_TOOLS, message.tool_call_id))
    return exchanges


def prune_old_tool_exchanges(
    messages: list[Message],
    *,
    keep_tool_rounds: int = 2,
    max_messages: int = 40,
) -> list[Message]:
    """Drop the oldest tool exchanges, keeping the most recent rounds.

    Returns the original list object when there is nothing to drop, so callers can
    detect a no-op with an identity check. Protected tool results (see
    :data:`PROTECTED_TOOLS`) are kept even when they are old.
    """
    exchanges = _tool_exchanges(messages)
    if len(exchanges) <= keep_tool_rounds:
        return messages

    keep_ids = {call_id for _, _, _, call_id in exchanges[-keep_tool_rounds:]}
    keep_ids |= {call_id for _, _, protected, call_id in exchanges if protected}
    drop_ids = {call_id for _, _, _, call_id in exchanges if call_id not in keep_ids}
    if not drop_ids:
        return messages

    drop_indexes: set[int] = set()
    for assistant_index, tool_index, _, call_id in exchanges:
        if call_id not in drop_ids:
            continue
        drop_indexes.add(tool_index)
        if assistant_index >= 0:
            drop_indexes.add(assistant_index)
        if len(drop_indexes) >= max_messages:
            break
    if not drop_indexes:
        return messages
    return [message for index, message in enumerate(messages) if index not in drop_indexes]
