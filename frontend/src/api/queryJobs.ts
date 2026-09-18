import { http } from "./http";
import { readToken } from "./token";
import type { AgentFusionResponse, Constraint } from "./agentFusion";

export type QueryMode = "auto" | "chat" | "fusion";
export type ResolvedMode = "chat" | "fusion";
export type JobStatus = "queued" | "running" | "completed" | "failed" | "cancel_requested" | "cancelled";
export interface QueryEvent { sequence: number; event_type: string; phase: string; status: string; agent?: string | null; model?: string | null; title: string; summary?: string | null; detail?: Record<string, unknown> | null; duration_ms?: number | null; created_at: string }
export interface ToolTrace { sequence: number; tool: string; arguments: Record<string, unknown>; query_id?: string | null; success: boolean; error_code?: string | null; truncated: boolean; duration_ms?: number | null; result_summary?: string | null }
export interface AgentTrace { agent_run_id: string; model_id?: string | null; status: string; error_code?: string | null; metrics: Record<string, unknown>; evidence_ids: string[]; tools: ToolTrace[] }
export interface QueryTrace { job_id: string; request_id: string; agents: AgentTrace[] }
export interface ChatJobResult { kind: "chat" | "chat_fallback" | "catalogue_advice"; conversation_id: string; answer: string; model: string; context_compressed: boolean; fusion_available?: boolean; verification?: "not_verified" | "capability_verified" }
export interface ProductInfoResult { kind: "product_info"; conversation_id: string; answer: string; model: string; context_compressed: boolean; product_name: string; sources: { title: string; url: string; field?: string; label?: string }[]; facts?: { field: string; label: string; value: string; unit: string; status?: "verified" | "catalogue_only"; evidence_ids?: string[] }[]; verification: "facts_verified" | "partial" | "not_verified" }
export type QueryResult = ChatJobResult | ProductInfoResult | ({ kind: "fusion" } & AgentFusionResponse);
export interface QueryJob { id: string; request_id: string; conversation_id?: string | null; requested_mode: QueryMode; resolved_mode?: ResolvedMode | null; status: JobStatus; result?: QueryResult | null; error_code?: string | null; error?: string | null; last_sequence: number; created_at: string; started_at?: string | null; completed_at?: string | null }
export interface CreateQueryJob { message: string; conversation_id?: string | null; constraints?: Constraint[]; top_k?: number; mode: QueryMode }

export async function createQueryJob(payload: CreateQueryJob): Promise<QueryJob> { return (await http.post<QueryJob>("/api/query/jobs", payload)).data }
export async function getQueryJob(id: string): Promise<QueryJob> { return (await http.get<QueryJob>(`/api/query/jobs/${id}`)).data }
export async function cancelQueryJob(id: string): Promise<QueryJob> { return (await http.post<QueryJob>(`/api/query/jobs/${id}/cancel`)).data }
export async function getQueryTrace(id: string): Promise<QueryTrace> { return (await http.get<QueryTrace>(`/api/query/jobs/${id}/trace`)).data }
export function queryEventsUrl(id: string, after: number): string {
  const base = String(import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
  // `EventSource` cannot set request headers, so the bearer token has to travel in
  // the query string. The server re-checks it periodically while the stream is open,
  // so a token revoked mid-run stops the stream instead of finishing it.
  const token = readToken();
  const query = [`after=${after}`, token ? `token=${encodeURIComponent(token)}` : ""].filter(Boolean).join("&");
  return `${base}/api/query/jobs/${id}/events?${query}`;
}
