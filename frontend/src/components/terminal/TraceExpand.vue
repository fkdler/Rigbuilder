<script setup lang="ts">
import { onMounted, ref } from "vue";
import { getQueryTrace, type QueryTrace } from "@/api/queryJobs";

const props = defineProps<{ jobId: string }>();
const trace = ref<QueryTrace | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const expandedAgent = ref<string | null>(null);
const expandedTool = ref<string | null>(null);

onMounted(async () => {
  try {
    trace.value = await getQueryTrace(props.jobId);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "无法读取执行轨迹。";
  } finally {
    loading.value = false;
  }
});

function toggleAgent(id: string) {
  expandedAgent.value = expandedAgent.value === id ? null : id;
}

function toggleTool(id: string) {
  expandedTool.value = expandedTool.value === id ? null : id;
}
</script>

<template>
  <div class="trace" aria-live="polite">
    <p v-if="loading" class="trace-muted">正在读取持久化执行轨迹…</p>
    <p v-else-if="error" class="trace-error">{{ error }}</p>
    <p v-else-if="!trace?.agents.length" class="trace-muted">此任务没有可用的 Agent 轨迹。</p>
    <section v-for="agent in trace?.agents ?? []" v-else :key="agent.agent_run_id" class="trace-agent">
      <button class="trace-agent__toggle" type="button" :aria-expanded="expandedAgent === agent.agent_run_id"
        @click="toggleAgent(agent.agent_run_id)">
        <span class="trace-state" :data-state="agent.status" aria-hidden="true"></span>
        <span>{{ agent.model_id || 'unknown model' }}</span>
        <span class="trace-status">{{ agent.status }}</span>
        <span class="trace-muted">{{ agent.tools.length }} tools</span>
      </button>
      <div v-if="expandedAgent === agent.agent_run_id" class="trace-rounds">
        <button v-for="tool in agent.tools" :key="tool.sequence" class="trace-tool" type="button"
          :aria-expanded="expandedTool === `${agent.agent_run_id}:${tool.sequence}`"
          @click="toggleTool(`${agent.agent_run_id}:${tool.sequence}`)">
          <span class="trace-muted">round {{ tool.sequence }}</span>
          <span>{{ tool.tool }}</span>
          <span :class="tool.success ? 'trace-ok' : 'trace-error'">{{ tool.success ? 'completed' : tool.error_code }}</span>
          <span v-if="tool.duration_ms != null" class="trace-muted">{{ tool.duration_ms }} ms</span>
          <span v-if="expandedTool === `${agent.agent_run_id}:${tool.sequence}`" class="trace-tool__detail">
            <code v-if="tool.arguments.sql">{{ tool.arguments.sql }}</code>
            <span v-if="tool.result_summary">{{ tool.result_summary }}</span>
            <span v-if="tool.truncated">结果已截断</span>
            <span v-if="agent.evidence_ids.length">Evidence {{ agent.evidence_ids.length }} 条</span>
          </span>
        </button>
        <p v-if="agent.error_code" class="trace-error">{{ agent.error_code }}</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.trace { width: min(100%, 68ch); margin: .45rem 0 .5rem 1.25rem; color: var(--color-terminal-fg); font: .7rem/1.55 var(--font-mono); }
.trace-agent { border-top: 1px solid var(--color-divider); }
.trace-agent__toggle, .trace-tool { width: 100%; min-height: 44px; border: 0; background: transparent; color: inherit; text-align: left; cursor: pointer; }
.trace-agent__toggle { display: flex; align-items: center; gap: .7rem; padding: .45rem 0; }
.trace-agent__toggle:focus-visible, .trace-tool:focus-visible { outline: 2px solid var(--color-accent-cyan); outline-offset: 2px; }
.trace-state { width: 6px; height: 6px; border-radius: 50%; background: var(--color-terminal-fg-dimmer); }
.trace-state[data-state="completed"], .trace-ok { color: var(--color-status-success); }
.trace-state[data-state="completed"] { background: var(--color-status-success); }
.trace-state[data-state="failed"], .trace-error { color: var(--color-status-error); }
.trace-state[data-state="failed"] { background: var(--color-status-error); }
.trace-status { margin-left: auto; color: var(--color-terminal-fg-dimmer); font-size: .64rem; }
.trace-muted { color: var(--color-terminal-fg-dimmer); }
.trace-rounds { margin-left: 1.35rem; padding-bottom: .5rem; }
.trace-tool { display: grid; grid-template-columns: 5rem minmax(8rem, 1fr) auto auto; gap: .7rem; padding: .4rem 0; }
.trace-tool__detail { grid-column: 1 / -1; display: grid; gap: .3rem; color: var(--color-terminal-fg-dim); overflow-wrap: anywhere; }
.trace-tool__detail code { display: block; white-space: pre-wrap; color: var(--color-terminal-fg); }
@media (max-width: 640px) { .trace-tool { grid-template-columns: 4.5rem 1fr auto; } .trace-tool > :nth-child(4) { display: none; } }
</style>
