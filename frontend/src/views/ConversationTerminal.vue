<script setup lang="ts">
import { computed, onMounted, watch } from "vue";
import { useRoute } from "vue-router";
import AppSidebar from "@/components/AppSidebar.vue";
import TerminalHeader from "@/components/terminal/TerminalHeader.vue";
import RecommendationBlock from "@/components/terminal/RecommendationBlock.vue";
import WorkProgress from "@/components/terminal/WorkProgress.vue";
import ProductIntroduction from "@/components/terminal/ProductIntroduction.vue";
import TerminalEmpty from "@/components/terminal/TerminalEmpty.vue";
import Composer from "@/components/terminal/Composer.vue";
import HardwareDaily from "@/components/hardware-daily/HardwareDaily.vue";
import { useAuthStore } from "@/stores/auth";
import { useConversationStore } from "@/stores/conversations";
import type { AgentFusionResponse } from "@/api/agentFusion";
import type { QueryEvent, QueryResult } from "@/api/queryJobs";

const store = useConversationStore();
const auth = useAuthStore();
const route = useRoute();
const currentModule = computed(() => route.query.module === "daily" ? "daily" : "terminal");

/**
 * Load this account's sessions only once there *is* an account.
 *
 * The terminal renders behind the login overlay, so mounting while signed out would
 * otherwise fire a list request that can only fail with 401 -- noise that looks like
 * a product error. The watcher is what recovers the session list after sign-in,
 * because that happens without a re-mount.
 */
function loadIfSignedIn(): void {
  if (auth.isAuthenticated) void store.hydrateOnLoad();
}

onMounted(loadIfSignedIn);
watch(() => auth.isAuthenticated, loadIfSignedIn);

watch(
  () => [currentModule.value, route.query.conversation] as const,
  ([module, requestedConversation]) => {
    if (module !== "terminal" || typeof requestedConversation !== "string") return;
    if (requestedConversation !== store.currentConversationId) void store.selectConversation(requestedConversation);
  },
  { immediate: true },
);

interface TimelineJob {
  id: string;
  message: string;
  status: string;
  /** What the request actually resolved to; null for jobs recorded before this existed. */
  resolvedMode: string | null;
  result: QueryResult | null;
  error: string | null;
  events: QueryEvent[];
  startedAt: number;
}

const timeline = computed<TimelineJob[]>(() => {
  const detailJobs = [...(store.currentDetail?.jobs ?? [])]
    .sort((a, b) => Date.parse(a.created_at ?? "") - Date.parse(b.created_at ?? ""))
    .map((job) => ({
      id: job.job_id,
      message: job.request_message,
      status: store.currentRunner.jobId === job.job_id
        ? store.currentRunner.status : store.jobsById.get(job.job_id)?.status ?? job.status,
      resolvedMode: store.jobsById.get(job.job_id)?.resolved_mode ?? job.resolved_mode ?? null,
      result: store.jobsById.get(job.job_id)?.result ?? job.result ?? null,
      error: (store.currentRunner.jobId === job.job_id ? store.currentRunner.error?.message : null)
        ?? store.jobsById.get(job.job_id)?.error ?? job.error ?? null,
      events: store.eventsByJob.get(job.job_id) ?? [],
      startedAt: store.currentRunner.jobId === job.job_id && store.currentRunner.startedAt !== null
        ? store.currentRunner.startedAt : Date.parse(job.created_at ?? "") || Date.now(),
    }));
  const runner = store.currentRunner;
  if ((runner.jobId || runner.status === "submitting" || runner.error) && !detailJobs.some((job) => job.id === runner.jobId)) {
    detailJobs.push({
      id: runner.jobId ?? "submitting",
      message: runner.requestMessage,
      status: runner.status,
      resolvedMode: runner.job?.resolved_mode ?? null,
      result: runner.job?.result ?? null,
      error: runner.error?.message ?? null,
      events: runner.events,
      startedAt: runner.startedAt ?? Date.now(),
    });
  }
  return detailJobs;
});

function fusionResult(result: QueryResult | null): AgentFusionResponse | null {
  if (!result || result.kind !== "fusion") return null;
  const { kind: _kind, ...fusion } = result;
  return fusion;
}

const isWorking = (status: string) => ["submitting", "queued", "running", "cancel_requested"].includes(status);
</script>

<template>
  <div class="terminal-shell">
    <AppSidebar />
    <div class="terminal-workspace">
      <HardwareDaily v-if="currentModule === 'daily' && auth.isAuthenticated" />
      <main v-else-if="currentModule === 'daily'" class="daily-sign-in" aria-label="硬件日报">
        登录后即可读取最新硬件日报。
      </main>
      <template v-else>
      <TerminalHeader />
      <main class="terminal-scroll" aria-label="会话内容">
        <div :key="store.currentConversationId ?? 'new-session'" class="terminal-stream">
          <TerminalEmpty v-if="!timeline.length" />

          <article v-for="job in timeline" :key="job.id" class="terminal-turn">
            <p class="terminal-prompt"><span aria-hidden="true">&gt;</span><span>{{ job.message }}</span></p>

            <RecommendationBlock v-if="fusionResult(job.result)" :job-id="job.id"
              :response="fusionResult(job.result)!"
              :natural-language="fusionResult(job.result)?.natural_language ?? null"
              :coverage="fusionResult(job.result)?.result?.trace.agent_coverage ?? null" />

            <ProductIntroduction v-else-if="job.result?.kind === 'product_info'" :result="job.result" />
            <div v-else-if="job.result?.kind === 'chat' || job.result?.kind === 'chat_fallback' || job.result?.kind === 'catalogue_advice'" class="terminal-answer">
              <p>{{ job.result.answer }}</p>

              
            </div>

            <p v-if="job.error" class="terminal-error" role="alert">{{ job.error }}</p>
            <div v-if="isWorking(job.status)" class="terminal-running">
              <WorkProgress v-if="!job.error" :status="job.status" :started-at="job.startedAt" :events="job.events" />
              <span v-if="job.status === 'cancel_requested'">Stopping&hellip;</span>
              <button v-if="store.currentConversationId" type="button"
                @click="store.cancelJob(store.currentConversationId)">取消任务</button>
            </div>
          </article>

          <p v-if="store.globalError" class="terminal-error" role="alert">{{ store.globalError.message }}</p>
        </div>
      </main>
      <Composer />
      <div class="sr-only" aria-live="polite">
        {{ store.currentRunner.status === 'running' ? 'Task running' : store.currentRunner.status === 'completed' ? 'Task complete' : '' }}
      </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.terminal-shell { display:flex; gap:14px; height:100dvh; overflow:hidden; padding:14px; background:var(--color-terminal-bg); color:var(--color-terminal-fg); }
.terminal-workspace { display:flex; flex:1; flex-direction:column; min-width:0; min-height:0; border: var(--border-width-frame) solid var(--color-border); border-radius:18px; overflow:hidden; background:var(--color-panel); background-clip: padding-box; }
.terminal-scroll { flex:1; min-height:0; overflow-y:auto; padding:28px 28px 48px; scrollbar-width:thin; scrollbar-color:var(--color-border-emphasis) transparent; }
.terminal-stream { width:min(100%,960px); margin:auto; }
.terminal-turn + .terminal-turn { margin-top:40px; }
.terminal-prompt { display:flex; gap:12px; padding:14px 18px; margin:0; border-radius:10px; background:var(--color-terminal-bg-elevated); font:500 15px/1.7 var(--font-sans); overflow-wrap:anywhere; }
.terminal-prompt > span:first-child { color:var(--color-status-success); font-family:var(--font-mono); }
.terminal-prompt > span:last-child { min-width:0; white-space:pre-wrap; }
.terminal-answer { margin:24px 16px; font-size:15px; line-height:1.85; }
.terminal-answer p { margin:0; white-space:pre-wrap; overflow-wrap:anywhere; }
.terminal-error { color:var(--color-status-error); margin:16px; overflow-wrap:anywhere; }
.terminal-running { display:flex; flex-wrap:wrap; align-items:center; gap:12px; margin:16px; color:var(--color-terminal-fg-dim); font:12px var(--font-mono); }
.terminal-running button { min-height:44px; border:0; background:none; color:var(--color-status-warning); cursor:pointer; }
.daily-sign-in { display:grid; flex:1; min-height:0; place-items:center; color:var(--color-terminal-fg-dim); font:12px var(--font-mono); }
@media(max-width:760px) { .terminal-shell { flex-direction:column; padding:8px; gap:8px; } .terminal-scroll { padding:20px 14px 32px; } .terminal-workspace { border-radius:14px; } }
</style>
