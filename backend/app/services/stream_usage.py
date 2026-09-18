"""Provider streaming transport and request-scoped, measured completion usage."""
from __future__ import annotations

import json
import time
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

import httpx

from app.services.events import EventSink, emit

usage_sink: ContextVar[EventSink | None] = ContextVar("llm_usage_sink", default=None)


async def read_completion_stream(client: httpx.AsyncClient, url: str, payload: dict,
                                 headers: dict, *, per_token_timings: bool) -> dict[str, Any]:
    # Import locally to avoid a cycle with the shared response/error parser.
    from app.services.llm import LLMServiceError, _provider_error

    sink = usage_sink.get()
    call_id = str(uuid4())
    model = payload["model"]
    body = {**payload, "stream": True, "stream_options": {"include_usage": True}}
    if per_token_timings:
        body["timings_per_token"] = True  # llama.cpp extension; disable for other providers.
    content: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    usage: dict[str, Any] = {}
    timings: dict[str, Any] = {}
    finish_reason = None
    last_count = 0
    last_emitted = float("-inf")
    completed = False

    def progress(event_type: str, count: int | None = None) -> None:
        emit(sink, event_type, "llm", "Token usage", status="running" if count is not None else "completed",
             model=model, detail={"call_id": call_id, "completion_tokens": count})

    try:
        async with client.stream("POST", url, json=body, headers=headers) as response:
            if response.is_error:
                await response.aread()
                raise _provider_error(response)
            # Some compatible servers ignore stream=true and return ordinary JSON.
            if "application/json" in response.headers.get("content-type", ""):
                await response.aread()
                return response.json()
            data: list[str] = []

            async def events():
                async for line in response.aiter_lines():
                    if not line:
                        if data:
                            yield "\n".join(data)
                            data.clear()
                    elif line.startswith("data:"):
                        data.append(line[5:].lstrip(" "))
                if data:
                    yield "\n".join(data)

            async for raw in events():
                if raw.strip() == "[DONE]":
                    completed = True
                    break
                chunk = json.loads(raw)
                if not isinstance(chunk, dict) or chunk.get("error"):
                    raise LLMServiceError("The inference stream returned an error", code="llm_invalid_response")
                if isinstance(chunk.get("model"), str):
                    model = chunk["model"]
                if isinstance(chunk.get("usage"), dict):
                    usage.update(chunk["usage"])
                if isinstance(chunk.get("timings"), dict):
                    timings.update(chunk["timings"])
                choices = chunk.get("choices") or []
                choice = next((item for item in choices if item.get("index", 0) == 0), None)
                if choice:
                    delta = choice.get("delta") or {}
                    if isinstance(delta.get("content"), str):
                        content.append(delta["content"])
                    for tool in delta.get("tool_calls") or []:
                        index = tool["index"]
                        entry = calls.setdefault(index, {"id": "", "type": "function",
                                                          "function": {"name": "", "arguments": ""}})
                        if tool.get("id"):
                            entry["id"] = tool["id"]
                        function = tool.get("function") or {}
                        for key in ("name", "arguments"):
                            if isinstance(function.get(key), str):
                                entry["function"][key] += function[key]
                    if choice.get("finish_reason") is not None:
                        finish_reason = choice["finish_reason"]
                        completed = True
                # Only a current, pre-finish provider counter qualifies as LIVE usage.
                # Never infer tokens from content, chunks, bytes, or final-only usage.
                current_usage = chunk.get("usage") or {}
                current_timings = chunk.get("timings") or {}
                if not isinstance(current_usage, dict) or not isinstance(current_timings, dict):
                    raise LLMServiceError("Malformed streaming usage", code="llm_invalid_response")
                if not current_timings and isinstance(current_usage.get("timings"), dict):
                    current_timings = current_usage["timings"]
                count = current_usage.get("completion_tokens")
                if count is None:
                    count = current_timings.get("predicted_n")
                if (not completed and type(count) is int and count > last_count
                        and time.monotonic() - last_emitted >= 0.5):
                    progress("llm_usage", count)
                    last_count = count
                    last_emitted = time.monotonic()
            if not completed:
                raise LLMServiceError("The inference stream ended before completion", code="llm_transport_error")
        return {"model": model, "usage": usage, "timings": timings, "choices": [{
            "finish_reason": finish_reason,
            "message": {"content": "".join(content), "tool_calls": [calls[i] for i in sorted(calls)]},
        }]}
    finally:
        # Clear the live counter even on cancellation, malformed output, or a disconnect.
        if last_count:
            progress("llm_usage_finished")
