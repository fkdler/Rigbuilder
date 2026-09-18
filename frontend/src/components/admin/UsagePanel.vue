<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { adminError, getUsage, number, type SiteUsage } from "@/api/adminWorkspace";
import ChartPanel from "./ChartPanel.vue";

const today = new Date(Date.now() + 8 * 3600_000).toISOString().slice(0, 10);
const end = ref(today);
const start = ref(new Date(new Date(today).getTime() - 29 * 86400_000).toISOString().slice(0, 10));
const data = ref<SiteUsage | null>(null);
const busy = ref(false);
const error = ref("");
const seconds = (value: number | null | undefined) => value == null ? "—" : `${value.toLocaleString("zh-CN", { maximumFractionDigits: 3 })} 秒`;
async function load() {
  busy.value = true; error.value = "";
  try { data.value = await getUsage(start.value, end.value); }
  catch (cause) { error.value = adminError(cause); data.value = null; }
  finally { busy.value = false; }
}
onMounted(load);
const option = computed(() => ({
  animation: false,
  grid: { left: 68, right: 20, top: 30, bottom: 45 },
  tooltip: { trigger: "axis", renderMode: "richText", valueFormatter: (value: number) => `${number(value)} Token` },
  xAxis: { type: "category", data: data.value?.by_day.map(d => d.day.slice(5)), axisLabel: { hideOverlap: true } },
  yAxis: { type: "value", minInterval: 1, name: "Token" },
  series: [{ name: "已记录用量", type: "bar", barMaxWidth: 28, itemStyle: { color: "#a2a6ad", borderRadius: [3, 3, 0, 0] }, data: data.value?.by_day.map(d => d.total_tokens) }],
}));
const timingOption = computed(() => ({
  animation: false,
  grid: { left: 68, right: 20, top: 50, bottom: 45 },
  legend: { data: ["平均响应", "平均执行", "平均启动等待"] },
  tooltip: { trigger: "axis", renderMode: "richText", valueFormatter: seconds },
  xAxis: { type: "category", data: data.value?.timing?.by_day.map(d => d.day.slice(5)), axisLabel: { hideOverlap: true } },
  yAxis: { type: "value", min: 0, name: "秒" },
  series: [
    { name: "平均响应", key: "avg_response_seconds", color: "#D97757", type: "solid" },
    { name: "平均执行", key: "avg_execution_seconds", color: "#a2a6ad", type: "dashed" },
    { name: "平均启动等待", key: "avg_start_wait_seconds", color: "#79c0ff", type: "dotted" },
  ].map(item => ({ name: item.name, type: "line", connectNulls: false,
    lineStyle: { color: item.color, type: item.type }, itemStyle: { color: item.color },
    data: data.value?.timing?.by_day.map(d => d[item.key as "avg_response_seconds" | "avg_execution_seconds" | "avg_start_wait_seconds"]),
  })),
}));
</script>

<template>
  <section class="admin-page" :aria-busy="busy">
    <header class="admin-heading"><div><h1>数据看板</h1><p>查看全站 Token 用量与咨询请求耗时，按北京时间统计。</p></div></header>
    <form class="admin-toolbar" @submit.prevent="load">
      <label>开始日期<input v-model="start" type="date" required :max="end" /></label>
      <label>结束日期<input v-model="end" type="date" required :min="start" /></label>
      <button class="admin-button" :disabled="busy">{{ busy ? '加载中…' : '查询用量' }}</button>
    </form>
    <p v-if="error" class="admin-error" role="alert">{{ error }}</p>
    <p v-if="busy" role="status" class="admin-muted">正在读取用量记录…</p>
    <template v-if="data && !busy">
      <dl class="usage-summary">
        <div><dt>全站累计 Token · 已记录</dt><dd>{{ number(data.total_tokens) }}</dd></div>
        <div><dt>所选日期 Token</dt><dd>{{ number(data.period_tokens) }}</dd></div>
        <div><dt>所选日期调用次数</dt><dd>{{ number(data.period_calls) }}</dd></div>
      </dl>
      <div class="admin-section-title"><h2>每日用量</h2><span>{{ data.start }} — {{ data.end }}</span></div>
      <ChartPanel :option="option" :height="300" :label="`${data.start} 至 ${data.end}，已记录 ${number(data.period_tokens)} Token；逐日数值可在下方展开。`" />
      <p v-if="!data.period_calls" class="admin-empty">所选日期还没有调用记录。</p>
      <p v-if="data.unknown_calls" class="admin-notice">累计 {{ number(data.unknown_calls) }} 次调用未返回完整用量（所选日期 {{ number(data.period_unknown_calls) }} 次），未计入 Token 合计。</p>
      <p class="admin-muted usage-note">{{ data.note }}</p>
      <details><summary>查看逐日明细</summary><div class="admin-table-wrap"><table><caption class="sr-only">每日 Token 用量</caption><thead><tr><th>日期</th><th>已记录 Token</th><th>调用次数</th><th>用量未知</th></tr></thead><tbody><tr v-for="day in data.by_day" :key="day.day"><td>{{ day.day }}</td><td>{{ number(day.total_tokens) }}</td><td>{{ number(day.calls) }}</td><td>{{ number(day.unknown_calls) }}</td></tr></tbody></table></div></details>
      <section class="timing-section" aria-label="请求耗时统计">
        <div class="admin-section-title"><h2>请求耗时</h2><span>所选日期 · 成功咨询任务</span></div>
        <template v-if="data.timing">
          <dl class="usage-summary timing-summary">
            <div><dt>平均响应耗时</dt><dd>{{ seconds(data.timing.avg_response_seconds) }}</dd></div>
            <div><dt>平均执行耗时</dt><dd>{{ seconds(data.timing.avg_execution_seconds) }}</dd></div>
            <div><dt>平均启动等待</dt><dd>{{ seconds(data.timing.avg_start_wait_seconds) }}</dd></div>
          </dl>
          <p class="admin-muted">有效样本：{{ number(data.timing.samples) }} 个成功请求；一次请求可能包含多次模型调用。</p>
          <ChartPanel v-if="data.timing.samples" :option="timingOption" :height="280" label="每日平均响应、执行与启动等待耗时，单位为秒；无有效样本的日期留空，逐日数值可在下方展开。" />
          <p v-else class="admin-empty">所选日期没有可统计耗时的成功请求。</p>
          <p class="admin-muted usage-note">{{ data.timing.note }}</p>
          <details><summary>查看逐日耗时</summary><div class="admin-table-wrap"><table><caption class="sr-only">每日请求耗时</caption><thead><tr><th>日期</th><th>有效请求</th><th>平均响应</th><th>平均执行</th><th>平均启动等待</th></tr></thead><tbody><tr v-for="day in data.timing.by_day" :key="day.day"><td>{{ day.day }}</td><td>{{ number(day.samples) }}</td><td>{{ seconds(day.avg_response_seconds) }}</td><td>{{ seconds(day.avg_execution_seconds) }}</td><td>{{ seconds(day.avg_start_wait_seconds) }}</td></tr></tbody></table></div></details>
        </template>
        <p v-else class="admin-notice">当前服务尚未提供耗时统计，请更新后端服务后重试。</p>
      </section>
    </template>
  </section>
</template>

<style scoped>
.usage-summary { display: flex; flex-wrap: wrap; gap: 28px 56px; margin: 32px 0 42px; padding-bottom: 30px; border-bottom: 1px solid var(--color-border); }
.usage-summary dt { color: var(--color-terminal-fg-dim); font-size: 13px; }
.usage-summary dd { margin: 8px 0 0; font: 500 clamp(24px, 3vw, 36px)/1.2 var(--font-mono); font-variant-numeric: tabular-nums; }
.usage-note { max-width: 75ch; margin: 24px 0; }
.timing-section { margin-top: 42px; }
.timing-summary { margin: 24px 0; }
</style>
