"""Read-only llama.cpp Router status helpers (single and per-endpoint)."""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.inference.profiles import ModelProfile
from app.inference.servers import probe_base_url


class RouterStatusError(RuntimeError):
    pass


def _is_loaded(item: dict[str, Any]) -> bool:
    status = item.get("status")
    if isinstance(status, dict):
        status = status.get("value") or status.get("status")
    if isinstance(status, str):
        return status.lower() in {"loaded", "ready", "active", "running"}
    return bool(item.get("loaded") or item.get("is_loaded"))


async def get_loaded_models() -> list[str]:
    """Return loaded model ids from the single configured Router endpoint."""
    settings = get_settings()
    probe = await probe_base_url(settings.llm_base_url, settings, filter_loaded=True)
    if not probe.reachable:
        raise RouterStatusError("Router /models is unavailable")
    return probe.loaded_models


async def get_loaded_models_for(profile: ModelProfile) -> list[str]:
    """Return loaded model ids for one per-Agent endpoint."""
    settings = get_settings()
    probe = await probe_base_url(profile.endpoint_url, settings, profile_id=profile.id, filter_loaded=True)
    if not probe.reachable:
        raise RouterStatusError(f"Endpoint {profile.id} /models is unavailable")
    return probe.loaded_models


async def loaded_first(model_ids: list[str]) -> tuple[list[str], str | None]:
    try:
        loaded = set(await get_loaded_models())
    except RouterStatusError as exc:
        return list(model_ids), str(exc)
    return [item for item in model_ids if item in loaded] + [item for item in model_ids if item not in loaded], None


__all__ = [
    "RouterStatusError",
    "get_loaded_models",
    "get_loaded_models_for",
    "loaded_first",
]
