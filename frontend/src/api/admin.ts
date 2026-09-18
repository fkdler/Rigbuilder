/**
 * Typed read-only client for the five `/api/admin/*` endpoints added by Plan V3.5.
 *
 * Two deliberate design choices, both required by Plan V3.6 §5.2 and defended in §7-5:
 *
 * 1. **These functions never throw.**  Every call resolves to an `AdminResult`, so a
 *    single failing endpoint cannot take the page down with an unhandled rejection.
 *    The dashboard renders each block independently, and the header health probe can
 *    fail silently.  This is the one place in `src/api` that does not propagate, and it
 *    is intentional.
 * 2. **Every optional metric is `| null`, never defaulted to 0.**  The backend reports a
 *    missing JSONB key as `null` because six of the 33 keys the loop writes appear in
 *    only a subset of historical runs; rendering those as 0 would state a measurement
 *    that was never taken.  Consumers must render `null` differently from `0`.
 */

import { http } from "./http";

export interface Percentiles {
  avg: number | null;
  p50: number | null;
  p95: number | null;
  maximum: number | null;
}

export interface AgentStats {
  model_id: string;
  runs: number;
  completed: number;
  failed: number;
  completion_rate: number | null;
  truth_verified: number;
  truth_verified_rate: number | null;
  errors: Record<string, number>;
  rounds: Percentiles;
  sql_calls: Percentiles;
  llm_calls: Percentiles;
  tool_calls: Percentiles;
  wall_clock_ms: Percentiles;
  total_duration_ms: Percentiles;
  agent_duration_ms: Percentiles;
  llm_duration_ms: Percentiles;
  prompt_eval_duration_ms: Percentiles;
  generation_duration_ms: Percentiles;
  queue_duration_ms: Percentiles;
  verification_duration_ms: Percentiles;
  fusion_duration_ms: Percentiles;
  tokens_per_second: number | null;
  tokens_per_second_coverage: number | null;
  first_run_at: string | null;
  last_run_at: string | null;
  observed_span_hours: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  llm_duration_total_ms: number | null;
  tool_duration_total_ms: number | null;
  context_pruned_messages: number | null;
  duplicate_statements_rejected: number | null;
  runs_with_usable_candidate_id: number | null;
  protocol_retries: number | null;
  metric_coverage: Record<string, number>;
}

export interface AgentRunRow {
  agent_run_id: string;
  model_id: string | null;
  status: string;
  error_code: string | null;
  created_at: string | null;
  updated_at: string | null;
  job_id: string | null;
  request_id: string | null;
  wall_clock_ms: number | null;
  total_duration_ms: number | null;
  agent_duration_ms: number | null;
  llm_duration_ms: number | null;
  queue_duration_ms: number | null;
  verification_duration_ms: number | null;
  rounds_used: number | null;
  sql_calls_used: number | null;
  llm_calls: number | null;
  tool_calls: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  truth_verified: boolean | null;
  token_floor: number | null;
}

export interface DayStats {
  day: string;
  runs: number;
  completed: number;
  failed: number;
  total_tokens: number | null;
}

export interface AgentStatsResponse {
  window_hours: number;
  since: string | null;
  model_filter: string | null;
  generated_at: string;
  elapsed_ms: number;
  total_runs: number;
  agents: AgentStats[];
  by_error_code: Record<string, number>;
  by_day: DayStats[];
  recent_runs: AgentRunRow[];
  parallel_runs: number;
  serial_runs: number;
}

export interface JobSummaryRow {
  job_id: string;
  status: string;
  resolved_mode: string | null;
  error_code: string | null;
  duration_ms: number | null;
  agent_coverage: number | null;
  event_count: number;
  created_at: string | null;
}

/**
 * Job-level aggregates.
 *
 * Note there is no `by_day` here: the day series lives on `AgentStatsResponse`, because
 * `agent_run.created_at` is what it truncates. Verified against the live endpoint rather
 * than assumed from the Python response model — an earlier revision of this file declared
 * `by_day` here and the dashboard silently rendered an empty "runs per day" chart.
 */
export interface JobStatsResponse {
  window_hours: number;
  since: string | null;
  generated_at: string;
  elapsed_ms: number;
  total_jobs: number;
  by_status: Record<string, number>;
  by_resolved_mode: Record<string, number>;
  by_error_code: Record<string, number>;
  duration_ms: Percentiles;
  coverage: Percentiles;
  events_by_type: Record<string, number>;
  fusion_jobs: number;
  fusion_with_zero_coverage: number;
  recent: JobSummaryRow[];
}

export interface EndpointStatus {
  profile_id: string;
  endpoint_url: string;
  configured_model_id: string;
  reachable: boolean;
  latency_ms: number | null;
  error: string | null;
  loaded_models: string[];
  capability_state: string;
  context_size: number | null;
  context_source?: "settings" | "props";
  configured_context_size?: number | null;
  max_tool_rounds: number | null;
  max_sql_calls: number | null;
  terminal_tool_supported: boolean | null;
  is_local: boolean | null;
}

export interface EndpointsResponse {
  generated_at: string;
  elapsed_ms: number;
  profiles: EndpointStatus[];
  distinct_endpoints: boolean;
  distinct_model_ids: boolean;
  configuration_issues: string[];
  parallel_capable: boolean;
  warnings: string[];
  agent_terminal_tool_supported: boolean | null;
  agent_parallel_required: boolean | null;
  capability_smoke_on_startup: boolean | null;
  agent_max_rounds: number | null;
  agent_max_sql_calls: number | null;
}

export interface ViewRowCount { view: string; rows: number | null; estimated: boolean; error: string | null }
export interface TableRowCount { table: string; rows: number | null; estimated: boolean; error: string | null }

export interface DatabaseOverviewResponse {
  generated_at: string;
  elapsed_ms: number;
  reachable: boolean;
  release_key: string | null;
  release_status: string | null;
  alembic_version: string | null;
  agent_view_count: number;
  truth_table_count: number;
  recommendable_entities: number | null;
  views: ViewRowCount[];
  empty_views: string[];
  key_tables: TableRowCount[];
  empty_key_tables: string[];
  warnings: string[];
}

export interface AdminSummaryResponse {
  generated_at: string;
  elapsed_ms: number;
  database: DatabaseOverviewResponse;
  endpoints: EndpointsResponse;
  agents: AgentStatsResponse;
  jobs: JobStatsResponse;
}

/** Discriminated result: the caller must handle failure, it can never be ignored by accident. */
export type AdminResult<T> = { ok: true; data: T } | { ok: false; error: string };

export interface AdminQuery {
  /** `0` means all time; the backend treats any value `<= 0` as no lower bound. */
  windowHours?: number;
  modelId?: string;
  limit?: number;
}

function describe(cause: unknown): string {
  if (cause instanceof Error) return cause.message;
  return String(cause);
}

async function get<T>(path: string, params: Record<string, unknown> = {}, timeoutMs?: number): Promise<AdminResult<T>> {
  try {
    const response = await http.get<T>(path, { params, ...(timeoutMs ? { timeout: timeoutMs } : {}) });
    return { ok: true, data: response.data };
  } catch (cause) {
    // Returned, not thrown: see the module docstring.
    return { ok: false, error: describe(cause) };
  }
}

function windowParams(query: AdminQuery): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  if (query.windowHours !== undefined) params.window_hours = query.windowHours;
  if (query.modelId) params.model_id = query.modelId;
  if (query.limit !== undefined) params.limit = query.limit;
  return params;
}

export function fetchAdminSummary(query: AdminQuery = {}): Promise<AdminResult<AdminSummaryResponse>> {
  return get<AdminSummaryResponse>("/api/admin/summary", windowParams(query));
}

export function fetchAgentStats(query: AdminQuery = {}): Promise<AdminResult<AgentStatsResponse>> {
  return get<AgentStatsResponse>("/api/admin/stats/agents", windowParams(query));
}

export function fetchJobStats(query: AdminQuery = {}): Promise<AdminResult<JobStatsResponse>> {
  return get<JobStatsResponse>("/api/admin/stats/jobs", windowParams(query));
}

export function fetchDatabaseOverview(): Promise<AdminResult<DatabaseOverviewResponse>> {
  return get<DatabaseOverviewResponse>("/api/admin/database");
}

/**
 * Endpoint reachability.  Called on every terminal page load, so it gets a shorter
 * timeout than the shared axios default: the backend caps its own probe at 12 s, and a
 * header indicator must never be the reason a page feels slow.
 */
export function fetchEndpoints(timeoutMs = 15_000): Promise<AdminResult<EndpointsResponse>> {
  return get<EndpointsResponse>("/api/admin/endpoints", {}, timeoutMs);
}
