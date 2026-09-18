<script setup lang="ts">
import { computed } from "vue";

import type { AgentStats, AgentStatsResponse, Percentiles } from "@/api/admin";
import ChartPanel from "./ChartPanel.vue";
import { dayTrendOption } from "./chartTheme";
import {
  MISSING, formatInt, formatMs, formatPercent, formatRatio, isPartial, metricCell, type MetricCell,
} from "./format";

const props = defineProps<{ stats: AgentStatsResponse }>();

/**
 * The daily run series lives on the Agent stats payload, not the job payload: it truncates
 * `agent_run.created_at`. Verified against the live endpoint rather than inferred from the
 * response models — the first revision read it off the job stats and drew an empty chart.
 */
const days = computed(() => props.stats.by_day ?? []);
const daySummary = computed(() => days.value
  .map((day) => `${day.day.slice(0, 10)}: ${day.runs} runs, ${day.completed} completed, ${day.failed} failed`)
  .join("; "));

interface MetricRow {
  label: string;
  /** Key inside `metric_coverage` that counts the runs carrying this metric. */
  coverageKey: string | null;
  format: (value: number | null | undefined) => string;
  percentiles: Percentiles;
}

function rowsFor(agent: AgentStats): MetricRow[] {
  return [
    { label: "rounds", coverageKey: "rounds_used", format: formatRatio, percentiles: agent.rounds },
    { label: "sql_calls", coverageKey: "sql_calls_used", format: formatRatio, percentiles: agent.sql_calls },
    { label: "llm_calls", coverageKey: "llm_calls", format: formatRatio, percentiles: agent.llm_calls },
    { label: "tool_calls", coverageKey: "tool_calls", format: formatRatio, percentiles: agent.tool_calls },
    // No coverage key: wall clock is taken from the owning job's stamps, not from metrics.
    { label: "wall_clock", coverageKey: null, format: formatMs, percentiles: agent.wall_clock_ms },
    { label: "agent_duration", coverageKey: "agent_duration_ms", format: formatMs, percentiles: agent.agent_duration_ms },
    { label: "llm_duration", coverageKey: "llm_duration_ms", format: formatMs, percentiles: agent.llm_duration_ms },
    { label: "queue_duration", coverageKey: "queue_duration_ms", format: formatMs, percentiles: agent.queue_duration_ms },
    { label: "verification", coverageKey: "verification_duration_ms", format: formatMs, percentiles: agent.verification_duration_ms },
    { label: "fusion", coverageKey: null, format: formatMs, percentiles: agent.fusion_duration_ms },
  ];
}

function cell(agent: AgentStats, row: MetricRow, pick: (p: Percentiles) => number | null): MetricCell {
  const key = row.coverageKey;
  const covered = key ? agent.metric_coverage[key] : undefined;
  // When the coverage map has no entry for the key, pass no coverage at all rather than 0:
  // a fabricated 0 would make a fully-recorded metric look partially recorded.
  const coverage = key && covered !== undefined ? { [key]: covered } : undefined;
  return metricCell(pick(row.percentiles), coverage, key ?? "", agent.runs, row.format);
}

const agents = computed(() => props.stats.agents.map((agent) => ({
  agent,
  rows: rowsFor(agent).map((row) => ({
    row,
    p50: cell(agent, row, (p) => p.p50),
    p95: cell(agent, row, (p) => p.p95),
    max: cell(agent, row, (p) => p.maximum),
  })),
})));

/** `metric_coverage` for a key, as "107/183", or null when the key never had a count. */
function coverageNote(cellValue: MetricCell): string | null {
  if (cellValue.covered === undefined) return null;
  return `${cellValue.covered}/${cellValue.total} runs`;
}

/**
 * Throughput is only reported when `llm_duration_ms` covered the whole window.  The backend
 * already returns null below that threshold; repeating the guard here means a partial rate
 * can never reach the screen even if the API's behaviour changes, because a rate computed
 * from a subset of runs looks precise while being wrong.
 */
const THROUGHPUT_COVERAGE_FLOOR = 0.95;

const tokensPerSecondPartial = (agent: AgentStats) =>
  (agent.tokens_per_second_coverage ?? 1) < THROUGHPUT_COVERAGE_FLOOR;

const tokensPerSecond = (agent: AgentStats) =>
  tokensPerSecondPartial(agent) ? MISSING : formatRatio(agent.tokens_per_second, 1);
</script>

<template>
  <div v-if="!agents.length" class="empty">窗口内没有 Agent 运行记录。</div>
  <div v-else class="agent-stats">
    <section class="panel">
      <h3 class="panel__title">runs per day</h3>
      <ChartPanel v-if="days.length" :option="dayTrendOption(days)" :height="200"
        :label="`每日 Agent 运行数，按完成与失败堆叠。${daySummary}`" />
      <p v-else class="panel__empty">窗口内没有按天记录。</p>
    </section>

    <section v-for="{ agent, rows } in agents" :key="agent.model_id" class="agent-block">
      <header class="agent-block__head">
        <h3>{{ agent.model_id }}</h3>
        <span class="agent-block__totals">
          <span>runs <b>{{ formatInt(agent.runs) }}</b></span>
          <span>completed <b>{{ formatInt(agent.completed) }}</b></span>
          <span>failed <b>{{ formatInt(agent.failed) }}</b></span>
          <span>success <b>{{ formatPercent(agent.completion_rate) }}</b></span>
          <span>truth verified <b>{{ formatPercent(agent.truth_verified_rate) }}</b></span>
        </span>
      </header>

      <table class="metric-table">
        <caption class="sr-only">{{ agent.model_id }} 的分位数指标，含覆盖率</caption>
        <thead>
          <tr>
            <th scope="col">metric</th>
            <th scope="col">p50</th>
            <th scope="col">p95</th>
            <th scope="col">max</th>
            <th scope="col">coverage</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="{ row, p50, p95, max } in rows" :key="row.label">
            <th scope="row">{{ row.label }}</th>
            <td>{{ p50.text }}</td>
            <td>{{ p95.text }}</td>
            <td>{{ max.text }}</td>
            <td class="coverage" :data-partial="isPartial(p50)">
              <!-- Coverage sits on the same row as the number so the two cannot be read apart. -->
              <template v-if="coverageNote(p50)">{{ coverageNote(p50) }}</template>
              <template v-else>n/a</template>
              <span v-if="isPartial(p50)" class="coverage__flag" title="该指标只覆盖窗口内部分运行">partial</span>
            </td>
          </tr>
        </tbody>
      </table>

      <dl class="agent-block__tokens">
        <div><dt>total_tokens</dt><dd>{{ formatInt(agent.total_tokens) }}</dd></div>
        <div><dt>prompt_tokens</dt><dd>{{ formatInt(agent.prompt_tokens) }}</dd></div>
        <div><dt>completion_tokens</dt><dd>{{ formatInt(agent.completion_tokens) }}</dd></div>
        <div>
          <dt>tokens/s</dt>
          <dd>
            {{ tokensPerSecond(agent) }}
            <span v-if="tokensPerSecondPartial(agent)" class="coverage__flag" title="llm_duration_ms 未覆盖窗口内全部运行，故不给出吞吐率">
              withheld
            </span>
            <span v-else-if="agent.tokens_per_second_coverage !== null" class="coverage__sub">
              {{ formatPercent(agent.tokens_per_second_coverage, 0) }} coverage
            </span>
          </dd>
        </div>
        <div><dt>context_pruned</dt><dd>{{ formatInt(agent.context_pruned_messages) }}</dd></div>
        <div><dt>duplicate_sql_rejected</dt><dd>{{ formatInt(agent.duplicate_statements_rejected) }}</dd></div>
        <div><dt>runs_with_candidate_id</dt><dd>{{ formatInt(agent.runs_with_usable_candidate_id) }}</dd></div>
      </dl>
    </section>

    <p class="agent-stats__footer">
      <span v-if="stats.model_filter">filtered to {{ stats.model_filter }} · </span>
      {{ formatInt(stats.total_runs) }} runs ·
      parallel {{ formatInt(stats.parallel_runs) }} / serial {{ formatInt(stats.serial_runs) }} ·
      aggregated in {{ formatMs(stats.elapsed_ms) }}
      <template v-if="stats.since"> · since {{ stats.since.slice(0, 16).replace('T', ' ') }}</template>
      <template v-else> · all time</template>
    </p>
  </div>
</template>

<style scoped>
.empty { color: var(--color-terminal-fg-dimmer); font: .75rem var(--font-mono); }
.agent-stats { display: grid; gap: 1.5rem; }
.panel { display: grid; gap: .5rem; align-content: start; }
.panel__title { margin: 0; color: var(--color-terminal-fg-dim); font: .72rem var(--font-mono); letter-spacing: .04em; }
.panel__empty { margin: 0; color: var(--color-terminal-fg-dimmer); font: .7rem var(--font-mono); }
.agent-block { display: grid; gap: .6rem; }
.agent-block__head { display: flex; flex-wrap: wrap; align-items: baseline; gap: .5rem 1.25rem; }
.agent-block__head h3 { margin: 0; color: var(--color-terminal-fg); font: 600 .9rem var(--font-mono); }
.agent-block__totals { display: flex; flex-wrap: wrap; gap: .35rem 1rem; color: var(--color-terminal-fg-dimmer); font: .68rem var(--font-mono); }
.agent-block__totals b { color: var(--color-terminal-fg-dim); font-weight: 500; font-variant-numeric: tabular-nums; }
.metric-table { width: 100%; border-collapse: collapse; font: .7rem var(--font-mono); }
.metric-table th, .metric-table td { padding: .3rem .6rem .3rem 0; text-align: right; }
.metric-table thead th { color: var(--color-terminal-fg-dimmer); font-weight: 500; border-bottom: 1px solid var(--color-divider); }
.metric-table tbody th { color: var(--color-terminal-fg-dim); font-weight: 400; text-align: left; }
.metric-table td { color: var(--color-terminal-fg); font-variant-numeric: tabular-nums; }
.coverage { color: var(--color-terminal-fg-dimmer); white-space: nowrap; }
.coverage[data-partial="true"] { color: var(--color-status-warning); }
.coverage__flag { margin-left: .4rem; padding: 0 .28rem; border: var(--border-width-frame) solid currentColor; border-radius: 2px; font-size: .6rem; background-clip: padding-box; }
.coverage__sub { margin-left: .4rem; color: var(--color-terminal-fg-dimmer); font-size: .62rem; }
.agent-block__tokens { display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); gap: .25rem 1.25rem; margin: .2rem 0 0; font: .68rem var(--font-mono); }
.agent-block__tokens > div { display: flex; justify-content: space-between; gap: .6rem; }
.agent-block__tokens dt { color: var(--color-terminal-fg-dimmer); }
.agent-block__tokens dd { margin: 0; color: var(--color-terminal-fg-dim); font-variant-numeric: tabular-nums; }
.agent-stats__footer { margin: 0; color: var(--color-terminal-fg-dimmer); font: .66rem var(--font-mono); }
@media (max-width: 620px) {
  /* `max` is dropped instead of coverage: hiding coverage on a narrow screen would leave a
     partially-recorded metric indistinguishable from a complete one, which is the single
     thing this table must never allow. */
  .metric-table th:nth-child(4), .metric-table td:nth-child(4) { display: none; }
  .metric-table { font-size: .66rem; }
  .metric-table th, .metric-table td { padding-right: .4rem; }
  .coverage { white-space: normal; }
  .coverage__flag { margin-left: .25rem; }
}
</style>
