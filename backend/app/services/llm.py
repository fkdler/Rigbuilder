from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from app.core.config import get_settings
from app.context.contracts import ChatMessage
from app.inference.http import inference_client
from app.services.timing import phase, timing
from app.services.stream_usage import read_completion_stream, usage_sink
from app.services.token_ledger import record_usage


class LLMConfigurationError(RuntimeError):
    """Raised when the local inference endpoint is invalid."""


class LLMServiceError(RuntimeError):
    """Raised when the provider cannot complete a request."""

    def __init__(self, message: str, *, code: str = "llm_error") -> None:
        super().__init__(message)
        self.code = code


class LLMProtocolError(LLMServiceError):
    """Raised when a model/template does not implement the requested protocol."""


@dataclass(frozen=True)
class LLMToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class LLMCallResult:
    content: str
    tool_calls: list[LLMToolCall]
    finish_reason: str | None
    model: str
    usage: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    call_mode: Literal["chat", "native_tools", "schema"] = "chat"


_native_capabilities: dict[str, bool] = {}


def native_capability(model_id: str) -> bool | None:
    return _native_capabilities.get(model_id)


def set_native_capability(model_id: str, supported: bool) -> None:
    _native_capabilities[model_id] = supported


def _provider_error(response: httpx.Response) -> LLMServiceError:
    detail = response.text[:600]
    if response.status_code in {400, 404, 405, 415, 422, 501}:
        return LLMProtocolError(
            f"The selected model/template rejected the native tool or schema protocol ({response.status_code}): {detail}"
        )
    lowered = detail.lower()
    code = "model_load_failed" if response.status_code >= 500 and any(
        marker in lowered for marker in ("load model", "loading model", "failed to load", "model load")
    ) else "router_http_error"
    return LLMServiceError(
        f"The inference Router returned HTTP {response.status_code} while loading or running the model.",
        code=code,
    )


async def complete_chat(
    messages: Sequence[dict[str, Any]],
    model_id: str | None = None,
    *,
    base_url: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    response_schema: dict[str, Any] | None = None,
    max_tokens: int | None = None,
    timeout_seconds: float | None = None,
    temperature: float | None = None,
) -> LLMCallResult:
    """Call llama.cpp with native tools or schema-constrained generation.

    ``base_url`` overrides the global endpoint (used by the three resident
    Agent servers and by the Narrator); when unset it falls back to the V3.2
    single-Router ``LLM_BASE_URL`` so existing callers are unaffected.
    """
    settings = get_settings()
    base_url = (base_url or settings.llm_base_url).rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise LLMConfigurationError("LLM_BASE_URL must start with http:// or https://")
    request_timeout = timeout_seconds if timeout_seconds is not None else settings.llm_timeout_seconds

    selected_model = model_id or settings.llm_model
    headers: dict[str, str] = {}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"

    call_mode: Literal["chat", "native_tools", "schema"] = "chat"
    payload: dict[str, Any] = {
        "model": selected_model,
        "messages": list(messages),
        "stream": False,
        "cache_prompt": True,
        "reasoning_effort": "none",
        "reasoning_budget": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if temperature is not None:
        payload["temperature"] = temperature
    if tools is not None:
        call_mode = "native_tools"
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
        payload["parallel_tool_calls"] = False
    if response_schema is not None:
        call_mode = "schema"
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "recommendation", "strict": True, "schema": response_schema},
        }

    started = time.perf_counter()
    result = None
    try:
        with phase("llm_http", model=selected_model, mode=call_mode):
            async with inference_client(request_timeout) as client:
                if settings.llm_stream_usage and usage_sink.get() is not None:
                    result = await read_completion_stream(
                        client, f"{base_url}/chat/completions", payload, headers,
                        per_token_timings=settings.llm_timings_per_token,
                    )
                else:
                    response = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
                    if response.is_error:
                        raise _provider_error(response)
                    result = response.json()
    except LLMServiceError:
        raise
    except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
        raise LLMServiceError(
            "The inference Router is unreachable at the configured address.", code="router_unavailable",
        ) from exc
    except httpx.ReadTimeout as exc:
        raise LLMServiceError(
            f"The inference request exceeded the {request_timeout:g}s response timeout.",
            code="llm_timeout",
        ) from exc
    except httpx.HTTPError as exc:
        raise LLMServiceError(
            "The connection to the inference Router failed.", code="llm_transport_error",
        ) from exc
    except ValueError as exc:
        raise LLMServiceError(
            "The inference Router returned malformed JSON.", code="llm_invalid_response",
        ) from exc
    finally:
        # Includes routing, compression, narration and every Agent round; a
        # failed/aborted call is recorded with unknown usage, never as zero.
        record_usage(result, selected_model)

    duration_ms = int((time.perf_counter() - started) * 1000)
    try:
        choice = result["choices"][0]
        message = choice["message"]
    except (IndexError, KeyError, TypeError) as exc:
        raise LLMServiceError("The local inference server returned an invalid response") from exc

    parsed_calls: list[LLMToolCall] = []
    raw_calls = message.get("tool_calls") or []
    if not isinstance(raw_calls, list):
        raise LLMProtocolError("The local inference server returned malformed tool_calls")
    for raw_call in raw_calls:
        try:
            function = raw_call["function"]
            parsed_calls.append(LLMToolCall(
                id=str(raw_call["id"]), name=str(function["name"]),
                arguments=str(function.get("arguments") or "{}"),
            ))
        except (KeyError, TypeError) as exc:
            raise LLMProtocolError("The local inference server returned malformed tool_calls") from exc

    content = message.get("content") or ""
    if not isinstance(content, str):
        content = str(content)
    if not content.strip() and not parsed_calls:
        raise LLMServiceError("The local inference server returned no text or tool call")

    response_model = result.get("model") or selected_model
    if not isinstance(response_model, str):
        response_model = selected_model
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    timings = result.get("timings") if isinstance(result.get("timings"), dict) else {}
    if not timings and isinstance(usage.get("timings"), dict):
        timings = usage["timings"]
    timing("llm_result", duration_ms, model=selected_model, mode=call_mode,
           prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"),
           prefill_ms=timings.get("prompt_ms"), decode_ms=timings.get("predicted_ms"),
           cached_tokens=timings.get("cache_n"))
    return LLMCallResult(
        content=content.strip(), tool_calls=parsed_calls, finish_reason=choice.get("finish_reason"),
        model=response_model, usage=usage, timings=timings, duration_ms=duration_ms, call_mode=call_mode,
    )


async def generate_reply(messages: Sequence[ChatMessage], model_id: str | None = None) -> tuple[str, str]:
    """Compatibility wrapper retained for /api/chat and existing callers."""
    result = await complete_chat(messages, model_id=model_id)
    if not result.content:
        raise LLMServiceError("The local inference server returned no text")
    return result.content, result.model


__all__ = [
    "LLMCallResult", "LLMConfigurationError", "LLMProtocolError", "LLMServiceError", "LLMToolCall",
    "complete_chat", "generate_reply", "native_capability", "set_native_capability",
]
