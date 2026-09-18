"""Discover per-slot llama.cpp context windows without mutating environment settings."""
import asyncio
import time
from dataclasses import replace

import httpx

from app.inference.http import inference_client

# endpoint/model -> (expiry, per-slot context or None). Failed probes have short TTL.
_windows: dict[tuple[str, str], tuple[float, int | None]] = {}


def context_from_props(payload, model_id):
    if not isinstance(payload, dict):
        return None
    alias = payload.get('model_alias')
    if alias and alias != model_id:
        return None  # Router /props may describe another loaded model.
    generation = payload.get('default_generation_settings')
    size = generation.get('n_ctx') if isinstance(generation, dict) else None
    # This is llama.cpp's slot window, not total KV capacity or training context.
    return size if type(size) is int and 0 < size <= 2**25 else None


def cached_context(endpoint, model_id, settings):
    if not getattr(settings, 'context_probe_enabled', False):
        return None
    entry = _windows.get((endpoint.rstrip('/'), model_id))
    return entry[1] if entry and entry[0] > time.monotonic() else None


async def resolve_context_window(profile, settings, *, force=False):
    if not getattr(settings, 'context_probe_enabled', False):
        return profile
    key = (profile.endpoint_url.rstrip('/'), profile.model_id)
    entry = _windows.get(key)
    if force or not entry or entry[0] <= time.monotonic():
        from app.inference.servers import _root_url, _headers
        size = None
        try:
            async with inference_client(min(settings.context_probe_timeout_seconds, settings.llm_timeout_seconds)) as client:
                response = await client.get(_root_url(profile.endpoint_url) + '/props', headers=_headers(settings))
                if response.status_code == 200:
                    size = context_from_props(response.json(), profile.model_id)
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        ttl = settings.context_probe_ttl_seconds if size else min(5, settings.context_probe_ttl_seconds)
        _windows[key] = (time.monotonic() + ttl, size)
    else:
        size = entry[1]
    fallback = profile.configured_context_size or profile.context_size
    return replace(profile, context_size=size or fallback, configured_context_size=fallback,
                   context_source='props' if size else 'settings')


async def refresh_context_windows(settings, *, force=False):
    from app.inference.profiles import build_profiles
    if not hasattr(settings, 'context_probe_enabled'):
        return []  # Legacy embedded callers supply only their required settings.
    return await asyncio.gather(*(resolve_context_window(p, settings, force=force) for p in build_profiles(settings)))


def effective_context_size(settings):
    from app.inference.profiles import build_profiles
    if not hasattr(settings, 'llm_test_model_id_list'):
        return settings.agent_default_context_size
    return min((p.context_size for p in build_profiles(settings)), default=settings.agent_default_context_size)
