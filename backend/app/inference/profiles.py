"""Model Capability Profiles for the two resident Agents (Plan V3.3).

The whole V3.3 inference layer reads model identity, endpoint and capability
from a profile instead of scattered ``settings.llm_base_url`` lookups.  Profiles
remain fully backward compatible: without ``AGENT_*_BASE_URL`` environment
variables every profile falls back to ``llm_base_url`` so the V3.2 single-Router
deployment keeps working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.config import Settings, get_settings

CapabilityState = Literal["ready", "tool_protocol_unsupported", "unverified", "skipped"]

PROFILE_ORDER: tuple[str, ...] = ("agent-a", "agent-b")
AGENT_COUNT = len(PROFILE_ORDER)

# profile id -> (endpoint setting name, model setting name)
_PROFILE_SETTING_KEYS: dict[str, tuple[str, str]] = {
    "agent-a": ("agent_a_base_url", "agent_a_model"),
    "agent-b": ("agent_b_base_url", "agent_b_model"),
}


@dataclass(frozen=True)
class ModelProfile:
    """Static and discovered facts about one inference endpoint."""

    id: str
    # OpenAI-compatible model identifier sent in the request body.
    model_id: str
    # Base URL without trailing slash, e.g. http://127.0.0.1:8081/v1
    endpoint_url: str
    context_size: int
    native_tools: bool = True
    multi_turn_tools: bool = True
    parallel_tools: bool = False
    thinking_enabled: bool = False
    terminal_tool_supported: bool = True
    chat_template: str | None = None
    max_tool_rounds: int = 3
    max_sql_calls: int = 4
    reserved_completion_tokens: int = 2_048
    capability: CapabilityState = "unverified"
    configured_context_size: int | None = None
    context_source: Literal['settings', 'props'] = 'settings'

    @property
    def is_endpoint_local(self) -> bool:
        return self.endpoint_url.startswith(("http://127.0.0.1", "http://localhost"))


def build_profiles(settings: Settings | None = None) -> list[ModelProfile]:
    """Build agent-a/b profiles in canonical order from one Settings source.

    Callers pass ``settings`` explicitly when it was obtained from a mocked or
    alternate source so registry lookups never read stale global state.
    """
    settings = settings or get_settings()
    model_ids = settings.llm_test_model_id_list
    context_size = settings.agent_default_context_size
    profiles: list[ModelProfile] = []
    # Only the fixed pair has profiles, including when an obsolete list is supplied.
    profile_order = PROFILE_ORDER[: len(model_ids)] if model_ids else PROFILE_ORDER
    for index, profile_id in enumerate(profile_order):
        base_url_key, model_key = _PROFILE_SETTING_KEYS[profile_id]
        base_url = getattr(settings, base_url_key) or settings.llm_base_url
        configured_model = getattr(settings, model_key)
        model_id = (
            configured_model
            or (model_ids[index] if index < len(model_ids) else None)
            or settings.llm_model
        )
        from app.inference.context_window import cached_context
        discovered_context = cached_context(base_url, model_id, settings)
        profiles.append(
            ModelProfile(
                id=profile_id,
                model_id=model_id,
                endpoint_url=base_url.rstrip("/"),
                context_size=discovered_context or context_size,
                configured_context_size=context_size,
                context_source='props' if discovered_context else 'settings',
                # Driven by AGENT_MAX_ROUNDS. The previous min(3, ...) cap left a
                # model that spent all three rounds on valid queries with no turn
                # left to call submit_recommendation.
                max_tool_rounds=settings.agent_max_rounds,
                max_sql_calls=settings.agent_max_sql_calls,
                terminal_tool_supported=settings.agent_terminal_tool_supported,
                reserved_completion_tokens=settings.prompt_reserved_completion_tokens,
            )
        )
    return profiles


def get_profile(profile_id: str, settings: Settings | None = None) -> ModelProfile | None:
    for profile in build_profiles(settings):
        if profile.id == profile_id:
            return profile
    return None


def profiles_by_id(settings: Settings | None = None) -> dict[str, ModelProfile]:
    return {profile.id: profile for profile in build_profiles(settings)}


def has_distinct_endpoints(profiles: list[ModelProfile]) -> bool:
    """True when every profile targets its own llama-server.

    A single Router deployment (all endpoints equal) must keep the serial
    execution path so concurrent requests cannot race `--models-max 1`.
    """
    return len(profiles) == AGENT_COUNT and len({profile.endpoint_url for profile in profiles}) == AGENT_COUNT


def parallel_configuration_issues(
    settings: Settings | None = None,
    model_ids: list[str] | None = None,
) -> list[str]:
    settings = settings or get_settings()
    profiles = build_profiles(settings)
    selected_model_ids = list(model_ids) if model_ids is not None else settings.llm_test_model_id_list
    # Diagnostics must reject stale three-model configurations rather than hide them.
    active_count = len(selected_model_ids)
    active_profiles = profiles[:active_count]
    issues: list[str] = []
    if active_count != AGENT_COUNT:
        issues.append("parallel mode requires exactly two configured Agent model IDs")
    if len(settings.llm_test_model_id_list) != AGENT_COUNT and active_count == AGENT_COUNT:
        issues.append("LLM_TEST_MODEL_IDS must contain exactly two model IDs")
    if len(active_profiles) != active_count or len({profile.endpoint_url for profile in active_profiles}) != active_count:
        issues.append(f"active Agent endpoints must be {active_count} distinct llama-server URLs")
    if len(set(selected_model_ids)) != active_count:
        issues.append("active Agent model IDs must be distinct")
    if [profile.model_id for profile in active_profiles] != selected_model_ids:
        issues.append("Agent profile model IDs must match LLM_TEST_MODEL_IDS in order")
    if settings.agent_parallel_required and not settings.capability_smoke_on_startup:
        issues.append("CAPABILITY_SMOKE_ON_STARTUP must be true when parallel mode is required")
    return issues


def settings_parallel_capable(
    settings: Settings | None = None,
    model_ids: list[str] | None = None,
) -> bool:
    """Whether the running configuration enables the active parallel path.

    Always False when the settings object is not a real Settings instance (for
    example test fakes) or when no distinct per-Agent endpoints are configured.
    """
    try:
        target = settings or get_settings()
        return not parallel_configuration_issues(target, model_ids)
    except (AttributeError, TypeError):
        return False


__all__ = [
    "CapabilityState",
    "ModelProfile",
    "PROFILE_ORDER",
    "build_profiles",
    "get_profile",
    "has_distinct_endpoints",
    "parallel_configuration_issues",
    "profiles_by_id",
]
