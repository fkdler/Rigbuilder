<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { fetchEndpoints, type EndpointStatus } from "@/api/admin";
import { useAuthStore } from "@/stores/auth";
import { useConversationStore } from "@/stores/conversations";

const store = useConversationStore();
const auth = useAuthStore();

const sessionTitle = computed(() => {
  if (!store.currentConversationId) return "new session";
  return store.conversations.find((item) => item.id === store.currentConversationId)?.title
    || `session ${store.currentConversationId.slice(0, 8)}`;
});


const statusLabel = computed(() => {
  const r = store.currentRunner;
  if (r.status === "idle" || r.status === "submitting") return "READY";
  if (["queued", "running", "cancel_requested"].includes(r.status)) return "RUNNING";
  if (r.status === "completed") return "DONE";
  return "ERROR";
});

/**
 * What the requested mode actually resolved to.
 *
 * `resolve_query_mode` inspects the message and only honours an explicit mode for
 * decision-style questions, so what the selector says is a request, not an outcome.  The
 * measured cost of getting that wrong is large: `fusion` p50 86.1 s against `chat` ~1 s.
 */
const resolvedMode = computed<string | null>(() => {
  const fromRunner = store.currentRunner.job?.resolved_mode;
  if (fromRunner) return fromRunner;
  const jobs = store.currentDetail?.jobs ?? [];
  for (let index = jobs.length - 1; index >= 0; index -= 1) {
    const mode = jobs[index]?.resolved_mode;
    if (mode) return mode;
  }
  return null;
});


// Endpoint health
// A dead llama-server used to surface only as `fusion_failed` after ~115 s; the database
// already holds 27 such failures.  /api/admin/endpoints answers this in ~20 ms, so the
// header can say it up front instead.
const endpoints = ref<EndpointStatus[] | null>(null);
const probing = ref(false);

const unreachable = computed(() => (endpoints.value ?? []).filter((profile) => !profile.reachable));

const endpointState = computed(() => {
  if (probing.value) return "probing";
  if (!endpoints.value) return "unknown";
  if (!endpoints.value.length) return "unknown";
  if (unreachable.value.length === endpoints.value.length) return "down";
  if (unreachable.value.length) return "degraded";
  return "ok";
});

const endpointSummary = computed(() => {
  const total = endpoints.value?.length ?? 0;
  if (!total) return probing.value ? "checking" : "unknown";
  return `${total - unreachable.value.length}/${total} online`;
});

const endpointDetail = computed(() => {
  if (endpointState.value === "degraded" || endpointState.value === "down") {
    return unreachable.value.map((profile) => `${profile.profile_id}: ${profile.error ?? "unreachable"}`).join("; ");
  }
  return (endpoints.value ?? []).map((profile) => `${profile.profile_id} ${profile.latency_ms ?? "?"} ms`).join(" · ");
});

/**
 * Probe only once there is an account, and again if one signs in.
 *
 * `/api/admin/endpoints` requires a login, so an unauthenticated mount would fire a
 * request that can only 401 -- invisible in the UI (the indicator just stays
 * "unknown") but present in every console and, worse, indistinguishable from a real
 * session expiry in the auth layer. The watcher is what covers signing in later,
 * because that happens without a re-mount.
 */
async function probe(): Promise<void> {
  if (!auth.isAuthenticated) return;
  probing.value = true;
  // Never throws: failures surface as `ok: false` and simply leave the indicator unknown.
  const result = await fetchEndpoints();
  if (result.ok) endpoints.value = result.data.profiles;
  probing.value = false;
}

onMounted(probe);
watch(() => auth.isAuthenticated, probe);
</script>

<template>
  <header class="terminal-header">
    <div class="terminal-header__inner">
      <div class="session-name"><strong>RigBuilder</strong><span aria-hidden="true">/</span><span class="session-title" :title="sessionTitle">{{ sessionTitle }}</span></div>
      <div class="header-controls">
        <span class="endpoint-state" :data-state="endpointState" :title="endpointDetail"><i aria-hidden="true"></i><span>{{ endpointSummary }}</span></span>
        <span class="mode-control" aria-label="运行模式" :title="resolvedMode ? `自动分流：${resolvedMode}` : '自动选择处理方式'">AUTO</span>
        <span class="runtime-state" :data-state="statusLabel.toLowerCase()">{{ statusLabel }}</span>
      </div>
    </div>
    <p v-if="endpointState === 'degraded' || endpointState === 'down'" class="endpoint-warning" role="alert">推理端点异常：{{ endpointDetail }}</p>
  </header>
</template>
<style scoped>
.terminal-header { flex: 0 0 auto; border-bottom: 1px solid var(--color-border); }
.terminal-header__inner { display:flex; align-items:center; justify-content:space-between; min-height:56px; gap:16px; padding:0 24px; font:12px var(--font-mono); }
.session-name { display:flex; align-items:center; gap:12px; min-width:0; color:var(--color-terminal-fg-dim); }
.session-name strong { color:var(--color-terminal-fg); font-size:14px; }
.session-title { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-family:var(--font-sans); }
.header-controls { display:flex; align-items:center; gap:16px; flex-shrink:0; }
.endpoint-state { display:flex; align-items:center; gap:6px; color:var(--color-terminal-fg-dim); }
.endpoint-state i { width:6px; height:6px; border-radius:50%; background:currentColor; }
.endpoint-state[data-state="ok"] { color:var(--color-status-success); }
.endpoint-state[data-state="down"], .runtime-state[data-state="error"] { color:var(--color-status-error); }
.endpoint-state[data-state="degraded"], .runtime-state[data-state="running"] { color:var(--color-status-warning); }
.mode-control, .runtime-state { font-size:11px; color:var(--color-terminal-fg-dim); }
.endpoint-warning { margin:0; padding:8px 24px; color:var(--color-status-error); font:12px/1.5 var(--font-mono); overflow-wrap:anywhere; }
@media(max-width:760px) { .terminal-header__inner { padding:0 14px; gap:8px; } .header-controls { gap:10px; } .session-title, .session-name > span { display:none; } .endpoint-state span { font-size:10px; } }
</style>
