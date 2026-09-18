"""Per-endpoint llama-server probing helpers (Plan V3.3).

Each Agent profile owns a separate llama-server.  These helpers give every
endpoint an independent health/readiness probe, request timeout and error
normalization so that a crash on `agent-a` can never be reported as an
`agent-b` / `agent-c` failure.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

import httpx

from app.core.config import Settings, get_settings
from app.inference.profiles import ModelProfile
from app.inference.http import inference_client
from app.services.timing import phase

ROUTER_MARKERS = ("load model", "loading model", "failed to load", "model load")


def _is_loaded(item: dict) -> bool:
    status = item.get("status")
    if isinstance(status, dict):
        status = status.get("value") or status.get("status")
    if isinstance(status, str):
        return status.lower() in {"loaded", "ready", "active", "running"}
    return bool(item.get("loaded") or item.get("is_loaded"))


def _root_url(endpoint_url: str) -> str:
    """Strip a trailing /v1 so /health and /models hit the llama-server root."""
    root = endpoint_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    return root


def _headers(settings: Settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}


@dataclass(frozen=True)
class EndpointProbe:
    profile_id: str | None
    reachable: bool
    loaded_models: list[str] = field(default_factory=list)
    error: str | None = None
    latency_ms: int | None = None


def classify_http_error(status_code: int, detail: str) -> tuple[str, str]:
    """Normalize one endpoint HTTP error into (code, safe message)."""
    if status_code in {400, 404, 405, 415, 422, 501}:
        return "llm_protocol_error", f"The endpoint rejected the protocol ({status_code}): {detail[:600]}"
    lowered = detail.lower()
    if status_code >= 500 and any(marker in lowered for marker in ROUTER_MARKERS):
        return "model_load_failed", "The model failed to load on the inference server."
    return "router_http_error", (
        f"The inference server returned HTTP {status_code} while loading or running the model."
    )


async def count_prompt_tokens(
    profile: ModelProfile,
    wire_messages: list[dict],
    settings: Settings | None = None,
    *,
    timeout_seconds: float = 20.0,
    wire_tools: list[dict] | None = None,
) -> int | None:
    """Count the server-rendered chat template, including tool schemas.

    /apply-template and /tokenize must both succeed; otherwise return None
    so callers use the shared estimate, never label JSON-text tokenization exact.
    Token counts are prompt accounting, not streamed output usage.
    """
    settings = settings or get_settings()
    base = _root_url(profile.endpoint_url)
    template = {'messages': wire_messages, 'add_generation_prompt': True,
                'chat_template_kwargs': {'enable_thinking': False}}
    if wire_tools:
        template['tools'] = wire_tools
    try:
        with phase("tokenize", model=profile.model_id):
            async with inference_client(min(timeout_seconds, settings.llm_timeout_seconds)) as client:
                rendered = await client.post(f"{base}/apply-template", json=template, headers=_headers(settings))
                if rendered.status_code != 200:
                    return None
                prompt = rendered.json().get('prompt')
                if not isinstance(prompt, str):
                    return None
                response = await client.post(f"{base}/tokenize", json={"content": prompt, 'add_special': False, 'parse_special': True},
                                             headers=_headers(settings))
                if response.status_code != 200:
                    return None
                tokens = response.json().get("tokens")
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return None
    return len(tokens) if isinstance(tokens, list) else None


async def probe_base_url(
    base_url: str,
    settings: Settings | None = None,
    *,
    timeout_seconds: float = 5.0,
    profile_id: str | None = None,
    filter_loaded: bool = False,
) -> EndpointProbe:
    """Probe one OpenAI-compatible endpoint and return its model ids.

    ``filter_loaded=True`` keeps the legacy multi-model Router semantics where
    `/models` lists every registered preset but only some are loaded.
    """
    settings = settings or get_settings()
    base = _root_url(base_url)
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=min(timeout_seconds, settings.llm_timeout_seconds)) as client:
            response = await client.get(f"{base}/models", headers=_headers(settings))
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        return EndpointProbe(
            profile_id=profile_id,
            reachable=False,
            error="router_unavailable",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    items = payload.get("data", payload.get("models", [])) if isinstance(payload, dict) else []
    loaded: list[str] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and (not filter_loaded or _is_loaded(item)):
            model = str(item.get("id") or item.get("model") or "")
            if model:
                loaded.append(model)
    return EndpointProbe(
        profile_id=profile_id,
        reachable=True,
        loaded_models=loaded,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


async def probe_endpoint(
    profile: ModelProfile,
    settings: Settings | None = None,
    *,
    timeout_seconds: float = 5.0,
) -> EndpointProbe:
    """Probe one Agent profile's endpoint."""
    return await probe_base_url(
        profile.endpoint_url,
        settings=settings,
        timeout_seconds=timeout_seconds,
        profile_id=profile.id,
    )


async def probe_profiles(
    profiles: list[ModelProfile],
    settings: Settings | None = None,
    *,
    timeout_seconds: float = 5.0,
) -> dict[str, EndpointProbe]:
    """Probe every profile concurrently; one endpoint failure cannot block others."""
    settings = settings or get_settings()
    results = await asyncio.gather(
        *(probe_endpoint(profile, settings, timeout_seconds=timeout_seconds) for profile in profiles)
    )
    return {probe.profile_id: probe for probe in results}


__all__ = [
    "EndpointProbe",
    "classify_http_error",
    "count_prompt_tokens",
    "probe_base_url",
    "probe_endpoint",
    "probe_profiles",
]
