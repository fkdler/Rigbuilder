<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import {
  fetchAdminSummary, fetchEndpoints,
  type AdminSummaryResponse, type EndpointsResponse,
} from "@/api/admin";
import AgentStatsTable from "@/components/admin/AgentStatsTable.vue";
import DatabasePanel from "@/components/admin/DatabasePanel.vue";
import EndpointPanel from "@/components/admin/EndpointPanel.vue";
import JobStatsPanel from "@/components/admin/JobStatsPanel.vue";
import MetricCard from "@/components/admin/MetricCard.vue";
import { formatCoverage, formatInt, formatMs, formatPercent } from "@/components/admin/format";
import AppSidebar from "@/components/AppSidebar.vue";
/**
 * Read-only operations console over the five `/api/admin/*` endpoints (Plan V3.5 §5).
 *
 * Every figure on this page comes straight from an endpoint; nothing is recomputed here.
 * That is deliberate — `agent_coverage` in particular is read from the stored job payload
 * so this page can never disagree with what the terminal displayed.
 *
 * Degradation is per section.  `/api/admin/summary` already isolates its four sections
 * server-side, and the endpoint probe is fetched separately so a stalled probe (the
 * backend caps it at 12 s) cannot delay the database and job figures.
 */

const WINDOWS = [
  { label: "all time", hours: 0 },
  { label: "24 h", hours: 24 },
  { label: "7 d", hours: 168 },
  { label: "30 d", hours: 720 },
] as const;

const windowHours = ref<number>(0);
const summary = ref<AdminSummaryResponse | null>(null);
const endpoints = ref<EndpointsResponse | null>(null);
const summaryError = ref<string | null>(null);
const endpointError = ref<string | null>(null);
const loading = ref(false);

const windowLabel = computed(() => WINDOWS.find((item) => item.hours === windowHours.value)?.label ?? `${windowHours.value} h`);

async function load() {
  loading.value = true;
  summaryError.value = null;
  endpointError.value = null;
  // Both requests are independent: neither may delay or sink the other.
  const [summaryResult, endpointsResult] = await Promise.all([
    fetchAdminSummary({ windowHours: windowHours.value, limit: 12 }),
    fetchEndpoints(),
  ]);
  if (summaryResult.ok) summary.value = summaryResult.data;
  else summaryError.value = summaryResult.error;
  if (endpointsResult.ok) endpoints.value = endpointsResult.data;
  else endpointError.value = endpointsResult.error;
  loading.value = false;
}

onMounted(load);

const jobs = computed(() => summary.value?.jobs ?? null);
const agents = computed(() => summary.value?.agents ?? null);
const database = computed(() => summary.value?.database ?? null);

/** Overview cards: the four numbers an operator checks first. */
const overview = computed(() => {
  const stats = jobs.value;
  if (!stats) return [];
  const failed = stats.by_status.failed ?? 0;
  return [
    { label: "total jobs", value: formatInt(stats.total_jobs), detail: null, tone: "default" as const },
    { label: "completed", value: formatInt(stats.by_status.completed ?? 0), detail: null, tone: "success" as const },
    {
      label: "failed", value: formatInt(failed),
      detail: stats.total_jobs ? `${formatPercent(failed / stats.total_jobs)} of all jobs` : null,
      tone: failed > 0 ? ("error" as const) : ("default" as const),
    },
    {
      label: "fusion / chat", value: `${formatInt(stats.by_resolved_mode.fusion ?? 0)} / ${formatInt(stats.by_resolved_mode.chat ?? 0)}`,
      detail: `${formatInt(stats.fusion_jobs)} fusion · ${formatInt(stats.fusion_with_zero_coverage)} with zero coverage`,
      tone: stats.fusion_with_zero_coverage > 0 ? ("warning" as const) : ("default" as const),
    },
    {
      label: "duration p50", value: formatMs(stats.duration_ms.p50),
      detail: `p95 ${formatMs(stats.duration_ms.p95)}`, tone: "default" as const,
    },
    {
      label: "coverage p50", value: formatCoverage(stats.coverage.p50),
      detail: `avg ${formatCoverage(stats.coverage.avg)}`, tone: "default" as const,
    },
  ];
});

const generatedAt = computed(() => summary.value?.generated_at ?? endpoints.value?.generated_at ?? null);
const elapsed = computed(() => summary.value?.elapsed_ms ?? null);
</script>

<template>
  <!-- Shares the terminal's shell so the sidebar, and therefore the way back, is present
       on every page this round introduces. -->
  <div class="admin-shell">
    <AppSidebar />
    <main class="admin-scroll" aria-label="运行统计">
      <div class="admin">
        <header class="admin__header">
      <div class="admin__title">
        <h1>Operations console</h1>
        <p>
          Read-only statistics over <code>agent_run</code>, <code>query_job</code>,
          <code>query_event</code> and the truth schema.
          <template v-if="elapsed !== null"> Aggregated in {{ formatMs(elapsed) }}.</template>
        </p>
      </div>
      <div class="admin__controls">
        <label class="window-control">
          window:
          <select :value="windowHours" aria-label="统计窗口"
            @change="windowHours = Number(($event.target as HTMLSelectElement).value); void load()">
            <option v-for="option in WINDOWS" :key="option.hours" :value="option.hours">{{ option.label }}</option>
          </select>
        </label>
        <button type="button" class="refresh" :disabled="loading" @click="load">
          {{ loading ? "loading…" : "refresh" }}
        </button>
      </div>
    </header>

    <!-- Each section degrades alone. A page-level message appears only when the summary
         call itself failed; the endpoint section can still succeed independently. -->
    <p v-if="summaryError" class="admin__alert" role="alert">
      无法读取统计汇总（{{ summaryError }}）。下方端点区块仍会独立尝试。
    </p>

    <section v-if="overview.length" class="admin__section" aria-label="概览">
      <h2 class="admin__section-title">overview · {{ windowLabel }}</h2>
      <div class="card-grid">
        <MetricCard v-for="card in overview" :key="card.label"
          :label="card.label" :value="card.value" :detail="card.detail" :tone="card.tone" />
      </div>
    </section>

    <section class="admin__section" aria-label="Agent 统计">
      <h2 class="admin__section-title">agents · {{ windowLabel }}</h2>
      <p v-if="summaryError" class="admin__section-error">统计汇总不可用：{{ summaryError }}</p>
      <AgentStatsTable v-else-if="agents" :stats="agents" />
    </section>

    <section class="admin__section" aria-label="任务统计">
      <h2 class="admin__section-title">jobs &amp; events · {{ windowLabel }}</h2>
      <p v-if="summaryError" class="admin__section-error">统计汇总不可用：{{ summaryError }}</p>
      <JobStatsPanel v-else-if="jobs" :stats="jobs" />
    </section>

    <section class="admin__section" aria-label="数据库概况">
      <h2 class="admin__section-title">database</h2>
      <p v-if="summaryError" class="admin__section-error">统计汇总不可用：{{ summaryError }}</p>
      <DatabasePanel v-else-if="database" :overview="database" />
    </section>

    <section class="admin__section" aria-label="推理端点">
      <h2 class="admin__section-title">inference endpoints</h2>
      <p v-if="endpointError" class="admin__section-error" role="alert">
        端点探测失败：{{ endpointError }}
      </p>
      <EndpointPanel v-else-if="endpoints" :endpoints="endpoints" />
    </section>

    <footer class="admin__footer">
      <span v-if="generatedAt">generated {{ generatedAt }}</span>
      <span>只读页面：不发任何写请求，不提供重跑入口。</span>
      <span v-if="summary">release data untouched · {{ formatInt(summary.database.recommendable_entities) }} recommendable entities</span>
    </footer>
      </div>
    </main>
  </div>
</template>

<style scoped>
.admin-shell { display: flex; height: 100dvh; overflow: hidden; background: var(--color-terminal-bg); color: var(--color-terminal-fg); }
.admin-scroll { flex: 1; min-width: 0; overflow-y: auto; scrollbar-color: var(--color-border-emphasis) var(--color-terminal-bg); scrollbar-width: thin; }
.admin { display: grid; gap: 2rem; width: min(100%, 108ch); margin: 0 auto; padding: 1.5rem 1.6rem 4rem; color: var(--color-terminal-fg); }
.admin__header { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between; gap: 1rem; padding-bottom: 1rem; border-bottom: 1px solid var(--color-border); }
.admin__title h1 { margin: 0; font: 600 1.1rem var(--font-mono); letter-spacing: -.015em; }
.admin__title p { max-width: 66ch; margin: .45rem 0 0; color: var(--color-terminal-fg-dimmer); font-size: .78rem; line-height: 1.6; }
.admin__title code { color: var(--color-terminal-fg-dim); font-family: var(--font-mono); font-size: .74rem; }
.admin__controls { display: flex; align-items: center; gap: .9rem; }
.window-control { display: flex; align-items: center; gap: .4rem; color: var(--color-terminal-fg-dimmer); font: .72rem var(--font-mono); }
.window-control select { border: 0; background: transparent; color: var(--color-status-success); font: inherit; font-weight: 600; cursor: pointer; }
.window-control select:focus-visible, .refresh:focus-visible { outline: 2px solid var(--color-accent-cyan); outline-offset: 3px; }
.refresh { min-height: 36px; padding: 0 .5rem; border: var(--border-width-frame) solid var(--color-border); border-radius: 2px; background: transparent; color: var(--color-terminal-fg-dim); font: .72rem var(--font-mono); cursor: pointer; background-clip: padding-box; }
.refresh:hover:not(:disabled) { border-color: var(--color-border-emphasis); color: var(--color-terminal-fg); }
.refresh:disabled { color: var(--color-terminal-fg-dimmer); cursor: progress; }
.admin__alert { margin: 0; padding: .6rem .8rem; border: var(--border-width-frame) solid var(--color-status-error-dim); border-radius: 2px; color: var(--color-status-error); font: .74rem/1.55 var(--font-mono); background-clip: padding-box; }
.admin__section { display: grid; gap: .85rem; }
.admin__section-title { margin: 0; padding-bottom: .35rem; border-bottom: 1px solid var(--color-divider); color: var(--color-status-success); font: 500 .76rem var(--font-mono); letter-spacing: .04em; }
.admin__section-error { margin: 0; color: var(--color-status-error); font: .72rem var(--font-mono); }
.card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); gap: .6rem; }
.admin__footer { display: flex; flex-wrap: wrap; gap: .4rem 1.5rem; padding-top: 1rem; border-top: 1px solid var(--color-border); color: var(--color-terminal-fg-dimmer); font: .66rem var(--font-mono); }
@media (max-width: 760px) {
  .admin-shell { flex-direction: column; }
  .admin { padding-inline: 1rem; }
}
</style>
