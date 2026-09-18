<script setup lang="ts">
import { computed } from "vue";

import type { JobStatsResponse } from "@/api/admin";
import ChartPanel from "./ChartPanel.vue";
import { MISSING, formatCoverage, formatDateTime, formatInt, formatMs } from "./format";
import { categoryBarOption, CHART_COLORS, countsToPoints } from "./chartTheme";

// Scalar job figures (totals, durations, coverage) live in the dashboard's overview bar and
// are deliberately not repeated here. The same number rendered twice on one screen reads as
// two different numbers the moment they disagree.
const props = defineProps<{ stats: JobStatsResponse }>();

const statusPoints = computed(() => countsToPoints(props.stats.by_status));
const modePoints = computed(() => countsToPoints(props.stats.by_resolved_mode));
// Error codes are the reason this dashboard exists: fusion_failed accounts for the bulk of
// the failed jobs, and it is invisible from the terminal.
const errorPoints = computed(() => countsToPoints(props.stats.by_error_code, 8));
const eventPoints = computed(() => countsToPoints(props.stats.events_by_type, 10));

// Defensive: a payload missing one optional array must not take the page down (Plan V3.6
// §7-5). The backend always sends these, so this is insurance rather than an expected case.
const recent = computed(() => props.stats.recent ?? []);

const statuses = computed(() => statusPoints.value.map((point) => `${point.label} ${point.value ?? MISSING}`).join(", "));
const errorSummary = computed(() => errorPoints.value.map((point) => `${point.label} ${point.value ?? MISSING}`).join(", "));
const eventSummary = computed(() => eventPoints.value.map((point) => `${point.label} ${point.value ?? MISSING}`).join(", "));
// Kept as a computed rather than inline: a nested template literal inside an attribute
// breaks the template compiler's attribute parsing and silently corrupts the render.
const modeSummary = computed(() => modePoints.value.map((point) => `${point.label} ${point.value ?? MISSING}`).join(", "));
</script>

<template>
  <div class="job-stats">
    <div class="chart-grid">
      <section class="panel">
        <h3 class="panel__title">job status</h3>
        <ChartPanel v-if="statusPoints.length" :option="categoryBarOption(statusPoints, { unit: 'jobs' })"
          :height="200" :label="`任务状态分布。${statuses}`" />
        <p v-else class="panel__empty">窗口内没有任务。</p>
      </section>

      <section class="panel">
        <h3 class="panel__title">error codes</h3>
        <ChartPanel v-if="errorPoints.length" :option="categoryBarOption(errorPoints, { unit: 'jobs', horizontal: true, color: CHART_COLORS.error })"
          :height="Math.max(160, errorPoints.length * 26)" :label="`错误码分布。${errorSummary}`" />
        <p v-else class="panel__empty">窗口内没有错误码记录。</p>
      </section>

      <section class="panel">
        <h3 class="panel__title">resolved mode</h3>
        <ChartPanel v-if="modePoints.length" :option="categoryBarOption(modePoints, { unit: 'jobs', color: CHART_COLORS.cyan })"
          :height="200" :label="`实际解析模式分布。${modeSummary}`" />
        <p v-else class="panel__empty">窗口内没有模式记录。</p>
      </section>

      <section class="panel">
        <h3 class="panel__title">event types</h3>
        <ChartPanel v-if="eventPoints.length" :option="categoryBarOption(eventPoints, { unit: 'events', horizontal: true, color: CHART_COLORS.cyan })"
          :height="Math.max(160, eventPoints.length * 22)" :label="`事件类型分布。${eventSummary}`" />
        <p v-else class="panel__empty">窗口内没有事件记录。</p>
      </section>
    </div>

    <section class="panel">
      <h3 class="panel__title">recent jobs</h3>
      <p v-if="!recent.length" class="panel__empty">窗口内没有任务。</p>
      <table v-else class="recent-table">
        <caption class="sr-only">最近任务</caption>
        <thead>
          <tr>
            <th scope="col">job</th>
            <th scope="col">status</th>
            <th scope="col">mode</th>
            <th scope="col">duration</th>
            <th scope="col">coverage</th>
            <th scope="col">events</th>
            <th scope="col">error</th>
            <th scope="col">created</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="job in recent" :key="job.job_id">
            <td class="mono-id">{{ job.job_id.slice(0, 8) }}</td>
            <td :data-state="job.status">{{ job.status }}</td>
            <td>{{ job.resolved_mode ?? MISSING }}</td>
            <td>{{ formatMs(job.duration_ms) }}</td>
            <!-- null means "not a fusion job", 0 means "fusion that found nothing": different facts. -->
            <td>{{ formatCoverage(job.agent_coverage) }}</td>
            <td>{{ formatInt(job.event_count) }}</td>
            <td>{{ job.error_code ?? MISSING }}</td>
            <td>{{ formatDateTime(job.created_at) }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<style scoped>
.job-stats { display: grid; gap: 1.5rem; }
.chart-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(20rem, 1fr)); gap: 1rem; }
.panel { display: grid; gap: .5rem; align-content: start; }
.panel__title { margin: 0; color: var(--color-terminal-fg-dim); font: 500 .72rem var(--font-mono); letter-spacing: .04em; text-transform: lowercase; }
.panel__empty { margin: 0; color: var(--color-terminal-fg-dimmer); font: .7rem var(--font-mono); }
.recent-table { width: 100%; border-collapse: collapse; font: .68rem var(--font-mono); }
.recent-table th, .recent-table td { padding: .3rem .7rem .3rem 0; text-align: left; white-space: nowrap; }
.recent-table thead th { color: var(--color-terminal-fg-dimmer); font-weight: 500; border-bottom: 1px solid var(--color-divider); }
.recent-table tbody td { color: var(--color-terminal-fg-dim); font-variant-numeric: tabular-nums; }
.recent-table td[data-state="failed"] { color: var(--color-status-error); }
.recent-table td[data-state="completed"] { color: var(--color-status-success); }
.mono-id { color: var(--color-terminal-fg-dimmer); }
@media (max-width: 760px) { .recent-table { display: block; overflow-x: auto; } }
</style>
