<script setup lang="ts">
import { computed } from "vue";
import type { QueryEvent } from "@/api/queryJobs";
import type { AgentRun } from "@/api/agentFusion";

/**
 * Execution summary for one turn.
 *
 * This heading used to be the hardcoded string "Verified fusion complete", shown
 * whenever the component had any agent row. A chat turn emits `llm_completed`
 * with a `model`, so the component built a row from it and the fast, unverified
 * single-model answer was labelled as a verified fusion. Measured: a
 * "recommend a whole gaming PC" prompt was answered by the fast path (no database
 * query, no truth verification) and the interface still claimed verification.
 *
 * The mode now comes from the job's own `resolved_mode`, and the two modes get
 * different headings. When `resolved_mode` is missing (jobs recorded before this
 * change), the fallback reads the events instead of assuming fusion.
 */
const props = withDefaults(defineProps<{
  events: QueryEvent[];
  running: boolean;
  agentResults?: AgentRun[];
  /** The job's resolved_mode; null/undefined for older jobs. */
  mode?: string | null;
}>(), {
  agentResults: () => [],
  mode: null,
});

interface AgentLine {
  key: string;
  model: string;
  status: "running" | "completed" | "failed" | "pending";
  sqlCalls: number;
  llmRounds: number;
  durationMs: number;
}

const isFusion = computed<boolean>(() => {
  if (props.mode === "fusion") return true;
  if (props.mode === "chat") return false;
  // No resolved_mode recorded: trust what the events actually show rather than
  // defaulting to the more flattering label.
  if (props.agentResults.length > 0) return true;
  return props.events.some((event) => event.event_type === "agent_started");
});

const agents = computed<AgentLine[]>(() => {
  if (!isFusion.value) return [];
  const map = new Map<string, AgentLine>();
  for (const e of props.events) {
    const key = e.agent ?? e.model ?? "";
    if (!key) continue;
    if (!map.has(key)) {
      map.set(key, { key, model: e.model ?? key, status: "pending", sqlCalls: 0, llmRounds: 0, durationMs: 0 });
    }
    const a = map.get(key)!;
    if (e.event_type === "agent_started") a.status = "running";
    if (e.event_type === "agent_completed") a.status = "completed";
    if (e.event_type === "agent_failed") a.status = "failed";
    if (e.event_type === "tool_completed" && e.detail?.tool === "query_database") a.sqlCalls++;
    if (e.event_type === "llm_completed") a.llmRounds++;
    if (e.duration_ms && e.phase === "agent") a.durationMs += e.duration_ms;
  }
  for (const result of props.agentResults) {
    const existing = map.get(result.model_id);
    if (existing) {
      existing.model = result.response_model ?? existing.model;
      existing.status = result.status;
      continue;
    }
    map.set(result.model_id, {
      key: result.model_id,
      model: result.response_model ?? result.model_id,
      status: result.status,
      sqlCalls: 0,
      llmRounds: 0,
      durationMs: 0,
    });
  }
  return [...map.values()];
});

/** A fast turn has no agent rows but still has activity worth reporting. */
const visible = computed<boolean>(() =>
  isFusion.value ? agents.value.length > 0 : props.events.length > 0,
);

/** New jobs use a fixed pair; historical jobs retain their recorded denominator. */
const agentTotal = computed<number | null>(() => {
  const started = props.events.find((event) => event.event_type === "fusion_started");
  const configured = started?.detail?.["agent_count"];
  if (typeof configured === "number" && configured > 0) return configured;
  return agents.value.length || null;
});

const agentLabel = (key: string) => key.match(/agent-([a-z])/i)?.[1]?.toUpperCase() ?? key.slice(0, 2).toUpperCase();
const statusText = (status: AgentLine["status"]) => status === "completed" ? "complete" : status;
</script>

<template>
  <section v-if="visible" class="agent-run" aria-label="执行状态">
    <h2 v-if="isFusion" class="run-heading" :data-running="running">
      <i aria-hidden="true"></i>{{ running ? 'Running verified fusion' : 'Verified fusion complete' }}
      <span v-if="agentTotal" class="run-heading__count">
        · {{ agentTotal }} {{ agentTotal === 1 ? "agent" : "agents" }}
      </span>
    </h2>
    <h2 v-else class="run-heading run-heading--fast" :data-running="running">
      <i aria-hidden="true"></i>{{ running ? 'Fast answer in progress' : 'Fast answer · unverified' }}
    </h2>
    <p v-if="!isFusion && !running" class="run-note">
      这次回答走的是快速问答：单模型直接作答，没有查询数据库，也没有经过真值检验。
    </p>
    <ol v-if="isFusion" class="agent-list">
      <li v-for="a in agents" :key="a.key" class="agent-row">
        <span class="agent-label">{{ agentLabel(a.key) }}</span>
        <span class="agent-model" :title="a.model">{{ a.model }}</span>
        <span class="agent-meta">
          <span v-if="a.sqlCalls">{{ a.sqlCalls }} SQL</span>
          <span v-if="a.durationMs">{{ (a.durationMs / 1000).toFixed(1) }}s</span>
        </span>
        <span class="agent-status" :data-state="a.status"><i aria-hidden="true"></i>{{ statusText(a.status) }}</span>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.agent-run { margin: 1rem 0 0 1.45rem; font-family: var(--font-mono); }
.run-heading { display: flex; align-items: center; gap: .6rem; margin: 0 0 .55rem; color: var(--color-terminal-fg-dim); font-size: .76rem; font-weight: 500; }
.run-heading i, .agent-status i { flex: 0 0 auto; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.run-heading { color: var(--color-status-success); }
.run-heading[data-running="true"] { color: var(--color-status-warning); }
/* A fast answer is not a failure, but it is not verified either: amber, never green. */
.run-heading--fast,
.run-heading--fast[data-running="true"] { color: var(--color-status-warning); }
.run-heading__count { color: var(--color-terminal-fg-dimmer); font-weight: 400; }
.run-note { max-width: 64ch; margin: 0 0 .4rem 1.15rem; color: var(--color-terminal-fg-dimmer); font: .7rem/1.55 var(--font-mono); }
.agent-list { display: grid; gap: .28rem; width: min(100%, 64ch); margin: 0; padding: 0 0 0 1.15rem; list-style: none; }
.agent-row { display: grid; grid-template-columns: 1.5rem minmax(0, 1fr) auto 5.4rem; align-items: center; gap: .7rem; min-height: 26px; font-size: .75rem; }
.agent-label { color: var(--color-terminal-fg-dimmer); font-weight: 700; }
.agent-model { overflow: hidden; color: var(--color-terminal-fg); text-overflow: ellipsis; white-space: nowrap; }
.agent-meta { display: flex; gap: .65rem; color: var(--color-terminal-fg-dimmer); font-size: .68rem; font-variant-numeric: tabular-nums; }
.agent-status { display: flex; align-items: center; gap: .45rem; color: var(--color-terminal-fg-dimmer); font-size: .68rem; }
.agent-status[data-state="completed"] { color: var(--color-status-success); }
.agent-status[data-state="failed"] { color: var(--color-status-error); }
.run-heading[data-running="true"] i, .agent-status[data-state="running"] i { animation: status-pulse 1.5s ease-in-out infinite; }
@keyframes status-pulse { 50% { opacity: .35; transform: scale(.72); } }
.agent-status[data-state="running"] { color: var(--color-status-warning); }
.agent-status[data-state="running"] i { animation: status-pulse 1.4s ease-in-out infinite; }
@keyframes status-pulse { 50% { opacity: .25; } }
@media (max-width: 620px) {
  .agent-run { margin-left: 1rem; }
  .agent-row { grid-template-columns: 1.25rem minmax(0, 1fr) 4.9rem; }
  .agent-meta { display: none; }
}
@media (prefers-reduced-motion: reduce) { .run-heading i, .agent-status i { animation: none !important; } }
</style>
