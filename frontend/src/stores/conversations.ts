import { defineStore } from "pinia";
import { computed, ref, markRaw } from "vue";
import { isAxiosError } from "axios";

import type { AgentFusionResponse, Constraint } from "@/api/agentFusion";
import type { ConversationDetail, ConversationSummary, NaturalLanguagePayload } from "@/api/conversations";
import { createConversation, deleteConversation, getConversationDetail, listConversations } from "@/api/conversations";
import { AUTH_STORAGE_KEYS, clearIdentityScopedStorage } from "@/api/token";
import {
  cancelQueryJob, createQueryJob, getQueryJob, queryEventsUrl,
  type JobStatus, type QueryEvent, type QueryJob, type QueryMode,
} from "@/api/queryJobs";

const CURRENT_KEY = AUTH_STORAGE_KEYS.CONVERSATION_KEY;
const MODE_KEY = "rigbuilder.recommendation.mode";
const TERMINAL = new Set<JobStatus>(["completed", "failed", "cancelled"]);

export interface JobRunner {
  jobId: string | null;
  status: WorkbenchStatus;
  job: QueryJob | null;
  events: QueryEvent[];
  error: { code?: string; message: string } | null;
  startedAt: number | null;
  source: EventSource | null;
  pollTimer: number | null;
  lastSequence: number;
  requestMessage: string;
}

export type WorkbenchStatus = "idle" | "submitting" | JobStatus;

function newRunner(): JobRunner {
  return {
    jobId: null, status: "idle", job: null, events: [], error: null,
    startedAt: null, source: null, pollTimer: null, lastSequence: 0,
    requestMessage: "",
  };
}

export const useConversationStore = defineStore("conversations", () => {
  // State and actions
  const conversations = ref<ConversationSummary[]>([]);
  const conversationDetails = ref<Map<string, ConversationDetail>>(new Map());
  const currentConversationId = ref<string | null>(localStorage.getItem(CURRENT_KEY));

  // State and actions
  const runners = ref<Map<string, JobRunner>>(new Map());
  const jobsById = ref<Map<string, QueryJob>>(new Map());
  const eventsByJob = ref<Map<string, QueryEvent[]>>(new Map());

  // State and actions
  const currentMode = ref<QueryMode>("auto");
  const composerText = ref("");
  const globalError = ref<{ code?: string; message: string } | null>(null);

  // State and actions
  const currentRunner = computed<JobRunner>(() => {
    const id = currentConversationId.value;
    if (!id) return newRunner();
    if (!runners.value.has(id)) runners.value.set(id, newRunner());
    return runners.value.get(id)!;
  });

  const currentDetail = computed<ConversationDetail | null>(() => {
    const id = currentConversationId.value;
    return id ? conversationDetails.value.get(id) ?? null : null;
  });

  const activeJobsByConversation = computed(() => {
    const map = new Map<string, boolean>();
    for (const [convId, runner] of runners.value) {
      map.set(convId, runner.status === "running" || runner.status === "queued" || runner.status === "cancel_requested");
    }
    for (const conv of conversations.value) {
      if (conv.running_job) map.set(conv.id, true);
    }
    return map;
  });

  let identityVersion = 0;

  // State and actions
  function runnerFor(conversationId: string): JobRunner {
    if (!runners.value.has(conversationId)) runners.value.set(conversationId, newRunner());
    return runners.value.get(conversationId)!;
  }

  function stopTransports(runner: JobRunner): void {
    runner.source?.close(); runner.source = null;
    if (runner.pollTimer !== null) window.clearInterval(runner.pollTimer);
    runner.pollTimer = null;
  }

  function applyJob(runner: JobRunner, next: QueryJob): void {
    runner.job = next;
    runner.error = null;
    if (runner.startedAt === null) {
      const timestamp = Date.parse(next.created_at);
      runner.startedAt = Number.isFinite(timestamp) ? timestamp : Date.now();
    }
    jobsById.value.set(next.id, next);
    runner.status = next.status;
    if (TERMINAL.has(next.status)) stopTransports(runner);
  }

  async function refreshRunner(conversationId: string, runner: JobRunner): Promise<void> {
    const version = identityVersion;
    const jobId = runner.jobId;
    if (!jobId) return;
    try {
      const next = await getQueryJob(jobId);
      if (version !== identityVersion || runner.jobId !== jobId) return;
      applyJob(runner, next);
      if (TERMINAL.has(runner.status as JobStatus)) {
        const detail = await getConversationDetail(conversationId);
        if (version !== identityVersion) return;
        conversationDetails.value.set(conversationId, detail);
      }
    }
    catch { runner.error = { code: "connection_failed", message: "无法读取任务状态，请检查后端服务。" }; }
  }

  function startPolling(conversationId: string, runner: JobRunner): void {
    runner.source?.close(); runner.source = null;
    if (runner.pollTimer !== null) return;
    runner.pollTimer = window.setInterval(() => void refreshRunner(conversationId, runner), 3_000);
    void refreshRunner(conversationId, runner);
  }

  function connectEvents(conversationId: string, runner: JobRunner): void {
    if (!runner.jobId || runner.source || TERMINAL.has(runner.status as JobStatus)) return;
    const jobId = runner.jobId;
    const source = markRaw(new EventSource(queryEventsUrl(jobId, runner.lastSequence)));
    runner.source = source;
    source.addEventListener("execution", (raw) => {
      if (runner.jobId !== jobId || runner.source !== source) return;
      try {
        const event = JSON.parse((raw as MessageEvent).data) as QueryEvent;
        if (runner.events.some((e) => e.sequence === event.sequence)) return;
        runner.events = [...runner.events, event].sort((a, b) => a.sequence - b.sequence);
        eventsByJob.value.set(runner.jobId!, runner.events);
        runner.lastSequence = Math.max(runner.lastSequence, event.sequence);
        if (["job_completed", "job_failed", "job_cancelled"].includes(event.event_type)) {
          runner.status = event.event_type === "job_completed" ? "completed" : event.event_type === "job_failed" ? "failed" : "cancelled";
          stopTransports(runner);
          void refreshRunner(conversationId, runner);
          void loadConversations();
        }
      } catch { /* ignore one malformed event */ }
    });
    source.onerror = () => {
      if (runner.jobId !== jobId || runner.source !== source) return;
      runner.error = { code: "connection_failed", message: "连接中断，正在重新获取任务状态。" };
      runner.source?.close(); runner.source = null;
      startPolling(conversationId, runner);
    };
  }

  async function loadConversations(): Promise<void> {
    const version = identityVersion;
    try {
      const rows = await listConversations();
      if (version !== identityVersion) return;
      conversations.value = rows;
    }
    catch (cause) {
      if (version !== identityVersion) return;
      globalError.value = { code: "conversation_list_failed", message: cause instanceof Error ? cause.message : "无法读取会话列表。" };
    }
  }

  async function selectConversation(id: string | null): Promise<void> {
    const version = identityVersion;
    currentConversationId.value = id;
    if (id) localStorage.setItem(CURRENT_KEY, id);
    else localStorage.removeItem(CURRENT_KEY);
    if (!id) return;
    try {
      const detail = await getConversationDetail(id);
      if (version !== identityVersion) return;
      conversationDetails.value.set(id, detail);
    }
    catch (cause) {
      if (version !== identityVersion) return;
      if (isAxiosError(cause) && cause.response?.status === 404) {
        const old = runners.value.get(id);
        if (old) stopTransports(old);
        runners.value.delete(id);
        conversationDetails.value.delete(id);
        if (currentConversationId.value === id) createNewSession();
        return;
      }
      globalError.value = { code: "conversation_detail_failed", message: cause instanceof Error ? cause.message : "无法恢复该会话。" };
      return;
    }
    // If a background job exists for this conversation, reconnect SSE
    const runner = runnerFor(id);
    if (runner.jobId && (runner.status === "running" || runner.status === "queued")) {
      connectEvents(id, runner);
    } else if (!runner.jobId) {
      // Check server for running jobs (e.g. page was refreshed)
      const detail = conversationDetails.value.get(id);
      const runningJob = detail?.jobs.find((j) => !TERMINAL.has(j.status as JobStatus));
      if (runningJob) {
        runner.jobId = runningJob.job_id;
        runner.status = runningJob.status as WorkbenchStatus;
        const timestamp = Date.parse(runningJob.created_at ?? "");
        runner.startedAt = Number.isFinite(timestamp) ? timestamp : Date.now();
        connectEvents(id, runner);
      }
    }
  }

  function createNewSession(): void {
    currentConversationId.value = null;
    localStorage.removeItem(CURRENT_KEY);
  }

  async function removeConversation(id: string): Promise<void> {
    const version = identityVersion;
    globalError.value = null;
    try { await deleteConversation(id); }
    catch (cause) {
      if (version !== identityVersion) return;
      globalError.value = { code: "conversation_delete_failed", message: cause instanceof Error ? cause.message : "删除失败，请先取消运行中的任务。" };
      throw cause;
    }
    if (version !== identityVersion) return;
    const runner = runners.value.get(id);
    if (runner) { stopTransports(runner); runners.value.delete(id); }
    conversationDetails.value.delete(id);
    conversations.value = conversations.value.filter((c) => c.id !== id);
    if (currentConversationId.value === id) createNewSession();
  }

  function setMode(_mode: QueryMode): void { currentMode.value = "auto"; localStorage.setItem(MODE_KEY, "auto"); }

  async function submit(message: string, conversationId: string | null,
                        customConstraints?: Constraint[], _overrideMode?: QueryMode): Promise<string | null> {
    const requestStartedAt = Date.now();
    const version = identityVersion;
    globalError.value = null;
    if (!conversationId) {
      try {
        const conversation = await createConversation();
        if (version !== identityVersion) return null;
        conversationId = conversation.id;
        conversationDetails.value.set(conversation.id, conversation);
        await selectConversation(conversation.id);
        if (version !== identityVersion) return null;
        await loadConversations();
        if (version !== identityVersion) return null;
      } catch (cause) {
        if (version !== identityVersion) return null;
        globalError.value = { code: "conversation_create_failed", message: cause instanceof Error ? cause.message : "无法创建会话。" };
        return null;
      }
    }
    const runner = runnerFor(conversationId);
    if (!message.trim() || ["submitting", "queued", "running", "cancel_requested"].includes(runner.status)) return conversationId;
    stopTransports(runner);
    runner.status = "submitting"; runner.error = null; runner.job = null; runner.jobId = null;
    runner.events = []; runner.lastSequence = 0; runner.startedAt = requestStartedAt;
    runner.requestMessage = message.trim();
    try {
      const created = await createQueryJob({
        message: message.trim(), conversation_id: conversationId,
        constraints: customConstraints ?? [], top_k: 3,
        mode: "auto",
      });
      if (version !== identityVersion) return null;
      runner.job = created; runner.status = created.status; runner.jobId = created.id;
      connectEvents(conversationId, runner);
      await refreshRunner(conversationId, runner);
      if (version !== identityVersion) return null;
      void loadConversations(); // refresh sidebar running indicator
    } catch (cause) {
      stopTransports(runner);
      runner.status = "failed";
      runner.error = { code: "submit_failed", message: cause instanceof Error ? cause.message : "任务提交失败。" };
    }
    return conversationId;
  }

  async function cancelJob(conversationId: string): Promise<void> {
    const version = identityVersion;
    const runner = runners.value.get(conversationId);
    if (!runner?.jobId || !["running", "queued", "cancel_requested"].includes(runner.status)) return;
    const previousStatus = runner.status;
    runner.status = "cancel_requested";
    try {
      const job = await cancelQueryJob(runner.jobId);
      if (version !== identityVersion) return;
      applyJob(runner, job);
    }
    catch {
      if (version !== identityVersion) return;
      if (runner.status === "cancel_requested") runner.status = previousStatus;
      runner.error = { code: "cancel_failed", message: "取消请求未送达，请重试。" };
    }
  }

  async function hydrateOnLoad(): Promise<void> {
    const version = identityVersion;
    await loadConversations();
    if (version !== identityVersion) return;
    await Promise.all(conversations.value.filter((item) => item.active_job_id).map(async (item) => {
      const runner = runnerFor(item.id);
      runner.jobId = item.active_job_id!;
      try {
        const job = await getQueryJob(item.active_job_id!);
        if (version !== identityVersion) return;
        applyJob(runner, job);
        connectEvents(item.id, runner);
      } catch {
        runner.error = { code: "job_restore_failed", message: "无法恢复后台任务状态。" };
      }
    }));
    if (version !== identityVersion) return;
    const lastId = currentConversationId.value;
    if (lastId) await selectConversation(lastId);
  }

  /**
   * Drop everything that belonged to the account that just signed out.
   *
   * Called on sign-out and on an account switch (Plan_V4.5 搂11.3 U3). Closing the
   * live transports is the part that is not optional: the runner map holds open
   * `EventSource` objects and polling timers, so without this a stream started by the
   * previous account would keep delivering events into the next account's view.
   * The list, the recovered detail payloads, the job/event caches and the
   * last-opened-session key are all identity-scoped and go with it.
   */
  function resetForSignOut(): void {
    identityVersion += 1;
    for (const runner of runners.value.values()) stopTransports(runner);
    runners.value = new Map();
    jobsById.value = new Map();
    eventsByJob.value = new Map();
    conversationDetails.value = new Map();
    conversations.value = [];
    currentConversationId.value = null;
    composerText.value = "";
    globalError.value = null;
    clearIdentityScopedStorage();
  }

  return {
    conversations, conversationDetails, currentConversationId, runners, jobsById, eventsByJob,
    currentMode, composerText, globalError,
    currentRunner, currentDetail, activeJobsByConversation,
    loadConversations, selectConversation, createNewSession, removeConversation,
    setMode, submit, cancelJob, hydrateOnLoad, resetForSignOut,
  };
});

