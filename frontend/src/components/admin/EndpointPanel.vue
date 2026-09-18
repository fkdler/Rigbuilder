<script setup lang="ts">
import { computed } from "vue";

import type { EndpointsResponse } from "@/api/admin";
import { MISSING, formatInt, formatMs } from "./format";

const props = defineProps<{ endpoints: EndpointsResponse }>();

const unreachable = computed(() => props.endpoints.profiles.filter((profile) => !profile.reachable));

/** Loop switches that previously required reading .env or querying the database. */
const switches = computed(() => [
  { label: "agent_terminal_tool_supported", value: props.endpoints.agent_terminal_tool_supported },
  { label: "agent_parallel_required", value: props.endpoints.agent_parallel_required },
  { label: "capability_smoke_on_startup", value: props.endpoints.capability_smoke_on_startup },
  { label: "parallel_capable", value: props.endpoints.parallel_capable },
  { label: "distinct_endpoints", value: props.endpoints.distinct_endpoints },
  { label: "distinct_model_ids", value: props.endpoints.distinct_model_ids },
]);

const shown = (value: boolean | null | undefined) => (value === null || value === undefined ? MISSING : String(value));
</script>

<template>
  <div class="endpoints">
    <p v-if="unreachable.length" class="endpoints__alert" role="alert">
      {{ unreachable.length }}/{{ endpoints.profiles.length }} 个推理端点不可达：
      {{ unreachable.map((profile) => `${profile.profile_id}（${profile.error ?? "无响应"}）`).join("；") }}
    </p>

    <table class="endpoint-table">
      <caption class="sr-only">推理端点状态</caption>
      <thead>
        <tr>
          <th scope="col">profile</th>
          <th scope="col">state</th>
          <th scope="col">latency</th>
          <th scope="col">model</th>
          <th scope="col">context</th>
          <th scope="col">rounds</th>
          <th scope="col">sql calls</th>
          <th scope="col">capability</th>
          <th scope="col">endpoint</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="profile in endpoints.profiles" :key="profile.profile_id" :data-reachable="profile.reachable">
          <th scope="row">{{ profile.profile_id }}</th>
          <td>
            <span class="dot" aria-hidden="true"></span>
            {{ profile.reachable ? "reachable" : "unreachable" }}
          </td>
          <td>{{ formatMs(profile.latency_ms) }}</td>
          <td>{{ profile.loaded_models.join(", ") || MISSING }}</td>
          <td :title="profile.context_source === 'props' ? '服务端 /props 返回的每槽窗口' : '配置回退值；未取得服务端窗口'">{{ formatInt(profile.context_size) }} · {{ profile.context_source === 'props' ? '服务端' : '配置' }}</td>
          <td>{{ formatInt(profile.max_tool_rounds) }}</td>
          <td>{{ formatInt(profile.max_sql_calls) }}</td>
          <td>{{ profile.capability_state }}</td>
          <td class="url">{{ profile.endpoint_url }}</td>
        </tr>
      </tbody>
    </table>

    <section class="panel">
      <h3 class="panel__title">runtime switches</h3>
      <ul class="switch-list">
        <li v-for="item in switches" :key="item.label">
          <span>{{ item.label }}</span>
          <span :data-on="item.value === true">{{ shown(item.value) }}</span>
        </li>
      </ul>
    </section>

    <section v-if="endpoints.configuration_issues.length || endpoints.warnings.length" class="panel">
      <h3 class="panel__title">configuration issues</h3>
      <ul class="warning-list">
        <li v-for="issue in endpoints.configuration_issues" :key="issue">{{ issue }}</li>
        <li v-for="warning in endpoints.warnings" :key="warning">{{ warning }}</li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.endpoints { display: grid; gap: 1.25rem; }
.endpoints__alert { margin: 0; color: var(--color-status-error); font: .72rem/1.5 var(--font-mono); }
.endpoint-table { width: 100%; border-collapse: collapse; font: .68rem var(--font-mono); }
.endpoint-table th, .endpoint-table td { padding: .3rem .8rem .3rem 0; text-align: left; white-space: nowrap; }
.endpoint-table thead th { color: var(--color-terminal-fg-dimmer); font-weight: 500; border-bottom: 1px solid var(--color-divider); }
.endpoint-table tbody th { color: var(--color-terminal-fg); font-weight: 500; }
.endpoint-table tbody td { color: var(--color-terminal-fg-dim); font-variant-numeric: tabular-nums; }
.endpoint-table tr[data-reachable="true"] td:first-of-type { color: var(--color-status-success); }
.endpoint-table tr[data-reachable="false"] td:first-of-type { color: var(--color-status-error); }
.dot { display: inline-block; width: 6px; height: 6px; margin-right: .35rem; border-radius: 50%; background: currentColor; }
.url { color: var(--color-terminal-fg-dimmer); }
.panel { display: grid; gap: .45rem; align-content: start; }
.panel__title { margin: 0; color: var(--color-terminal-fg-dim); font: 500 .72rem var(--font-mono); letter-spacing: .04em; }
.switch-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); gap: .2rem 1.25rem; margin: 0; padding: 0; list-style: none; font: .68rem var(--font-mono); }
.switch-list li { display: flex; justify-content: space-between; gap: .75rem; color: var(--color-terminal-fg-dim); }
.switch-list li span:first-child { color: var(--color-terminal-fg-dimmer); }
.switch-list li span[data-on="true"] { color: var(--color-status-success); }
.warning-list { display: grid; gap: .2rem; margin: 0; padding: 0 0 0 1.1rem; color: var(--color-status-warning); font: .68rem/1.5 var(--font-mono); }
@media (max-width: 900px) { .endpoint-table { display: block; overflow-x: auto; } }
</style>
