from functools import lru_cache
from pathlib import Path
from pydantic import Field

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    """Runtime configuration loaded from local environment files."""

    app_name: str = "RigBuilder API"
    app_environment: str = "development"
    database_url: str

    # Read-only PostgreSQL account for query_database(). This account must have
    # only USAGE/SELECT on the agent_catalog V3 views (see
    # backend/scripts/create_agent_readonly.py). When unset, query_database()
    # fails closed instead of falling back to the privileged engine.
    agent_readonly_database_url: str | None = None


    # Local llama-server connection (Inference Server).
    llm_base_url: str = "http://127.0.0.1:8080/v1"
    llm_model: str = "local-llm"
    llm_api_key: str | None = None
    llm_timeout_seconds: float = 120
    llm_stream_usage: bool = True
    llm_timings_per_token: bool = True
    # Comma-separated IDs for the two recommendation Agents.
    # This remains server-side so browsers cannot select arbitrary models.
    llm_test_model_ids: str = ""
    # Required before automatic context compression can be enabled. A future
    # Agent-to-model mapping will provide this per selected model.
    llm_context_window_tokens: int | None = None

    # Comma-separated browser origins allowed to call FastAPI directly.
    frontend_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    # V2.2 SQL Validator and read-only execution limits. The validator derives
    # every enforcement threshold below from this single Settings source.
    sql_max_length: int = 10_000
    sql_max_joins: int = 4
    sql_max_subquery_depth: int = 3
    sql_max_select_columns: int = 40
    sql_max_rows: int = 100
    sql_statement_timeout_ms: int = 3000
    sql_max_evidence_ids: int = 100
    sql_max_cell_chars: int = 500
    # Maximum serialized character budget for the Observation carried back to the
    # model as a ToolResult. When the result is larger, rows/evidence are trimmed
    # and the model is told to narrow the query instead of resending a huge blob.
    tool_max_result_chars: int = 12_000

    # V3.3 per-Agent inference endpoints. Every value is optional: when unset the
    # profile falls back to llm_base_url / llm_test_model_ids / llm_model so the
    # V3.2 single-Router deployment keeps working without new environment vars.
    agent_a_base_url: str | None = None
    agent_b_base_url: str | None = None
    agent_a_model: str | None = None
    agent_b_model: str | None = None
    # Model profile used by the natural-language Narrator after fusion finishes.
    narrator_profile_id: str = "agent-b"
    # Qwen3.5 is normally agent-b; analysis is serialized before dispatch.
    routing_analysis_enabled: bool = True
    routing_profile_id: str = "agent-b"
    routing_timeout_seconds: float = 20
    # Fallback context window used before a real llama-server /props probe is
    # available (offline development). Per-server values override at runtime.
    agent_default_context_size: int = 12_288
    context_probe_enabled: bool = True
    context_probe_timeout_seconds: float = Field(default=2.0, gt=0, le=10)
    context_probe_ttl_seconds: float = Field(default=30.0, gt=0, le=300)
    # Reserved completion tokens held back by the context preflight so a
    # generation budget never overflows n_ctx.
    prompt_reserved_completion_tokens: int = 2_048
    # Context pruning. Measured on this deployment: a three-Agent fusion accumulated
    # enough tool results to reach 17 527 and 18 353 real prompt tokens against a
    # 16 384 window, so the preflight had to reject the round and the Agent ended
    # with tool_protocol_error before it could submit. When the preflight would fail,
    # the loop now drops the oldest tool exchanges and keeps this many recent ones.
    context_keep_tool_rounds: int = 2
    # Upper bound on how many messages one pruning pass may remove, so a single pass
    # cannot discard the whole conversation.
    context_prune_max_messages: int = 40
    # Run capability smoke tests for every configured profile at startup. It is
    # off by default so development without installed weights never blocks.
    capability_smoke_on_startup: bool = False
    # When true, startup fails unless two distinct endpoints and model IDs are
    # configured and capability smoke is enabled. Keep false for serial dev.
    agent_parallel_required: bool = False
    # Read-only connection pool sized for 3 concurrent Agents each issuing up to
    # agent_max_sql_calls read-only queries.
    readonly_pool_size: int = 10
    readonly_max_overflow: int = 10

    # Local account system (Plan_V4.5 §11). Registration is self-service by default
    # so the first account can be created without shell access; an operator who
    # wants closed enrolment sets AUTH_ALLOW_REGISTRATION=false and creates accounts
    # with backend/scripts/create_user.py instead.
    auth_allow_registration: bool = True
    # Bearer-token lifetime. Long enough that a local workstation is not asked to
    # log in daily, short enough that a leaked token is not permanent.
    auth_session_ttl_hours: int = 336
    # Login throttling. Two-keyed (account and client) and in-process; see the note
    # in app/services/auth.py for why that is sufficient for a single-worker box.
    auth_login_max_attempts: int = 8
    auth_login_window_seconds: int = 300

    # Zhihu hardware daily.  The access secret remains server-side: this service
    # exposes a curated article record, never the upstream credential or raw
    # search response.  The cache is deliberately global because every signed-in
    # reader is looking at the same daily issue.
    zhihu_access_secret: str | None = None
    zhihu_daily_query: str = "显卡日报"
    zhihu_daily_author_name: str = "Wallace"
    zhihu_daily_author_profile_url: str = "https://www.zhihu.com/people/mimi-86-49"
    zhihu_daily_cache_ttl_seconds: int = Field(default=21_600, ge=3_600, le=86_400)
    zhihu_daily_timeout_seconds: float = Field(default=10.0, gt=0, le=30)

    # V1.1 Agent and context budgets.    # Hard upper bounds for the bounded Agent state machine. Native mode spends
    # up to agent_max_rounds tool rounds before the terminal tool must be called;
    # legacy mode uses at most four total rounds.
    agent_max_rounds: int = 4
    agent_max_sql_calls: int = 4
    # Local-deployment switch. When false the Agent is given only the database
    # tools and finishes through the JSON-schema finalize path instead of the
    # submit_recommendation terminal tool: 8B/4B local models frequently cannot
    # emit valid tool arguments for the wide Recommendation schema, while a
    # schema-constrained completion still succeeds. Default keeps V3.x behavior.
    agent_terminal_tool_supported: bool = True
    agent_total_timeout_seconds: float = 900
    # End-to-end budget for a two-Agent fusion request, including scheduler
    # queue time. Keep this below the browser's fusion timeout.
    fusion_total_timeout_seconds: float = 540
    # Agent count is fixed at two in inference.profiles; old tuning env keys are ignored.
    context_compression_ratio: float = 0.70
    context_recent_turns: int = 6

    @property
    def frontend_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]

    @property
    def llm_test_model_id_list(self) -> list[str]:
        return [model_id.strip() for model_id in self.llm_test_model_ids.split(",") if model_id.strip()]

    model_config = SettingsConfigDict(
        # Keep reading the existing root .env during migration; backend/.env
        # overrides it when both files exist.
        env_file=(PROJECT_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
