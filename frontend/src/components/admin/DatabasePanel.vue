<script setup lang="ts">
import { computed } from "vue";

import type { DatabaseOverviewResponse } from "@/api/admin";
import MetricCard from "./MetricCard.vue";
import { MISSING, formatInt } from "./format";

const props = defineProps<{ overview: DatabaseOverviewResponse }>();

const populatedViews = computed(() => props.overview.views.filter((view) => (view.rows ?? 0) > 0));
</script>

<template>
  <div class="database">
    <div class="card-grid">
      <MetricCard label="release" :value="overview.release_key ?? MISSING"
        :detail="overview.release_status ? `status ${overview.release_status}` : null" />
      <MetricCard label="migration" :value="overview.alembic_version ?? MISSING" />
      <MetricCard label="agent_catalog views" :value="formatInt(overview.agent_view_count)"
        :detail="`${overview.empty_views.length} empty`" />
      <MetricCard label="truth tables" :value="formatInt(overview.truth_table_count)"
        :detail="`${overview.empty_key_tables.length} empty`" />
      <MetricCard label="recommendable entities" :value="formatInt(overview.recommendable_entities)" />
    </div>

    <p v-if="!overview.reachable" class="database__alert" role="alert">
      数据库不可达：以下内容可能不完整。
    </p>

    <!-- "Which capabilities have no data yet" is the question this section answers, so the
         empty lists are given the same prominence as the populated ones. -->
    <div class="split">
      <section class="panel">
        <h3 class="panel__title">empty views ({{ overview.empty_views.length }})</h3>
        <p v-if="!overview.empty_views.length" class="panel__empty">没有空视图。</p>
        <ul v-else class="chip-list">
          <li v-for="view in overview.empty_views" :key="view">{{ view }}</li>
        </ul>
      </section>

      <section class="panel">
        <h3 class="panel__title">empty truth tables ({{ overview.empty_key_tables.length }})</h3>
        <p v-if="!overview.empty_key_tables.length" class="panel__empty">没有空表。</p>
        <ul v-else class="chip-list">
          <li v-for="table in overview.empty_key_tables" :key="table">{{ table }}</li>
        </ul>
      </section>
    </div>

    <section class="panel">
      <h3 class="panel__title">view row counts</h3>
      <table class="row-table">
        <caption class="sr-only">agent_catalog 各视图行数</caption>
        <thead>
          <tr><th scope="col">view</th><th scope="col">rows</th></tr>
        </thead>
        <tbody>
          <tr v-for="view in populatedViews" :key="view.view">
            <td>{{ view.view }}</td>
            <td>
              <!-- An estimated count is labelled as such; an unavailable one stays missing. -->
              {{ formatInt(view.rows) }}
              <span v-if="view.estimated" class="flag" title="按 pg_class.reltuples 估算">est</span>
              <span v-if="view.error" class="flag flag--error" :title="view.error">error</span>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-if="overview.key_tables.length" class="panel">
      <h3 class="panel__title">truth table row counts</h3>
      <ul class="table-counts">
        <li v-for="table in overview.key_tables" :key="table.table">
          <span>{{ table.table }}</span>
          <span>
            {{ formatInt(table.rows) }}
            <span v-if="table.estimated" class="flag">est</span>
          </span>
        </li>
      </ul>
    </section>

    <section v-if="overview.warnings.length" class="panel">
      <h3 class="panel__title">warnings</h3>
      <ul class="warning-list">
        <li v-for="warning in overview.warnings" :key="warning">{{ warning }}</li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.database { display: grid; gap: 1.5rem; }
.card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); gap: .6rem; }
.split { display: grid; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); gap: 1rem; }
.panel { display: grid; gap: .45rem; align-content: start; }
.panel__title { margin: 0; color: var(--color-terminal-fg-dim); font: 500 .72rem var(--font-mono); letter-spacing: .04em; }
.panel__empty { margin: 0; color: var(--color-terminal-fg-dimmer); font: .7rem var(--font-mono); }
.chip-list { display: flex; flex-wrap: wrap; gap: .35rem; margin: 0; padding: 0; list-style: none; }
.chip-list li { padding: .15rem .45rem; border: var(--border-width-frame) solid var(--color-border); border-radius: 2px; color: var(--color-status-warning); font: .68rem var(--font-mono); background-clip: padding-box; }
.row-table { width: 100%; border-collapse: collapse; font: .68rem var(--font-mono); }
.row-table th, .row-table td { padding: .25rem .7rem .25rem 0; text-align: left; }
.row-table td:last-child, .row-table th:last-child { text-align: right; font-variant-numeric: tabular-nums; }
.row-table thead th { color: var(--color-terminal-fg-dimmer); font-weight: 500; border-bottom: 1px solid var(--color-divider); }
.row-table tbody td { color: var(--color-terminal-fg-dim); }
.table-counts { display: grid; grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr)); gap: .2rem 1.25rem; margin: 0; padding: 0; list-style: none; font: .68rem var(--font-mono); }
.table-counts li { display: flex; justify-content: space-between; gap: .75rem; color: var(--color-terminal-fg-dim); }
.table-counts li span:first-child { color: var(--color-terminal-fg-dimmer); }
.flag { margin-left: .35rem; padding: 0 .25rem; border: var(--border-width-frame) solid currentColor; border-radius: 2px; color: var(--color-terminal-fg-dimmer); font-size: .6rem; background-clip: padding-box; }
.flag--error { color: var(--color-status-error); }
.warning-list { display: grid; gap: .2rem; margin: 0; padding: 0 0 0 1.1rem; color: var(--color-status-warning); font: .68rem/1.5 var(--font-mono); }
.database__alert { margin: 0; color: var(--color-status-error); font: .72rem var(--font-mono); }
</style>
