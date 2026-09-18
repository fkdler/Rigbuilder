import { http } from "./http";
import type { QueryResult } from "./queryJobs";
import type { CoreBuild } from "./agentFusion";

export interface ConversationSummary {
  id: string;
  status: string;
  title: string;
  last_message_summary: string;
  updated_at: string;
  running_job: boolean;
  latest_job_status?: string | null;
  active_job_id?: string | null;
}

export interface ConversationMessageInfo {
  sequence_no: number;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
}

export interface ConversationJobInfo {
  job_id: string;
  status: string;
  request_message: string;
  resolved_mode?: string | null;
  created_at?: string | null;
  completed_at?: string | null;
  error_code?: string | null;
  error?: string | null;
  result?: QueryResult | null;
  result_natural_language?: NaturalLanguagePayload | null;
  agent_coverage?: number | null;
  agent_summary: { agent_run_id?: string; model_id?: string; status?: string; error_code?: string | null; error?: string | null }[];
  trace_summary?: Record<string, unknown> | null;
}

/**
 * Narration as it arrives on a stored job.
 *
 * The conclusion fields are optional because rows written before Plan_V4.1 only
 * carry `overview` + `per_candidate`.
 */
export interface NaturalLanguagePayload {
  overview: string;
  per_candidate: { candidate_id: string; explanation: string }[];
  headline?: string | null;
  primary?: { candidate_id: string; name: string; reasons: string[] } | null;
  selections?: Array<{ candidate_id: string; name: string; label: string; reasons: string[] }>;
  selection_notice?: string | null;
  evidence?: string[];
  sources?: { title: string; url: string; field?: string }[];
  alternatives?: { candidate_id: string; name: string; note: string }[];
  caveats?: string[];
  // Additive in Plan_V4.2. Carried on the narration too, because a stored job may expose
  // only `natural_language` and the core build would otherwise be lost on reload.
  core_build?: CoreBuild | null;
}

export interface ConversationDetail {
  id: string;
  status: string;
  messages: ConversationMessageInfo[];
  jobs: ConversationJobInfo[];
}

export async function listConversations(): Promise<ConversationSummary[]> {
  return (await http.get<ConversationSummary[]>("/api/conversations")).data;
}

export async function createConversation(): Promise<ConversationDetail> {
  return (await http.post<ConversationDetail>("/api/conversations")).data;
}

export async function getConversationDetail(id: string): Promise<ConversationDetail> {
  return (await http.get<ConversationDetail>(`/api/conversations/${id}`)).data;
}

export async function deleteConversation(id: string): Promise<void> {
  await http.delete(`/api/conversations/${id}`);
}
