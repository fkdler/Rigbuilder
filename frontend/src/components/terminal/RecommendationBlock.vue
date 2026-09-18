<script setup lang="ts">
import { computed } from "vue";
import type { AgentFusionResponse } from "@/api/agentFusion";
import type { NaturalLanguagePayload } from "@/api/conversations";
const props = defineProps<{ response: AgentFusionResponse; jobId: string; naturalLanguage?: NaturalLanguagePayload | null; coverage?: number | null }>();
const candidates = computed(() => props.response.result?.top_k ?? []);
const isModel = computed(() => candidates.value[0]?.candidate_type === 'ai_model' || candidates.value[0]?.candidate_type === 'model_variant');
const presentation = computed(() => props.response.presentation ?? props.naturalLanguage ?? props.response.natural_language);
const build = computed(() => presentation.value?.core_build);
const roles: Record<string,string> = { cpu:"CPU", gpu:"GPU", platform:"主板平台", memory:"内存", storage:"存储", psu:"电源", display:"显示方案", cooler:"散热", case:"机箱" };
const omittedRoles = new Set(["storage", "cooler", "case"]);
function conciseText(text: string): string {
  return text.replace(/（(?:来源估计，尚未完整核验；非本机实测|来源值已核对，非本机实测|目录与来源的估计值，字段核验未通过；非本机实测)）/g, "")
    .replace(/当前不保证运行速度或全部装入显存。|文件记录不等于部署验证。|仅构成单方面建议，您可以多方求证选择心仪配置。/g, "");
}
const visibleGaps = computed(() => (build.value?.gaps ?? []).filter(line => !/存储|散热|机箱/.test(line)));
const configuration = computed(() => build.value?.core.length ? [
  ...build.value.core.map(item => ({ label: roles[item.role], name: item.name })),
  ...build.value.supporting.filter(item => !omittedRoles.has(item.role)).map(item => ({ label: roles[item.role] ?? item.role, name: item.spec })),
] : presentation.value?.selections?.length ? presentation.value.selections.map(item => ({ label: item.label, name: item.name }))
  : [{ label: "", name: presentation.value?.primary?.name ?? candidates.value[0]?.canonical_name ?? "" }]);
const reason = computed(() => {
  if (presentation.value?.selections?.length) return presentation.value.selections.map(item => `${item.name}：${conciseText(item.reasons.join(" "))}`).join(" ");
  const lines = presentation.value?.primary?.reasons?.length ? presentation.value.primary.reasons : build.value?.core.flatMap(item => item.reasons) ?? [];
  return conciseText([...new Set(lines)].join(" ")) || "具体规格与来源列于下方，便于结合用途和预算比较。";
});
const fields: Record<string,string> = { "gpu.vram_gib":"显存", "gpu.board_power_w":"板卡功耗", "gpu.architecture":"架构", "gpu.memory_type":"显存类型", "gpu.memory_bandwidth_gb_s":"显存带宽", "cpu.cores_total":"核心数", "cpu.threads":"线程数", "cpu.socket":"插槽", "cpu.base_power_w":"基础功耗" };
Object.assign(fields, { "entity.canonical_name": "产品名称", "model.total_parameters": "参数量", "model.context_length_tokens": "上下文长度", "model.license_name": "许可证", "model.deployment_estimated_load_gb": "加载占用估计", "model.deployment_min_ram_gb": "系统内存参考", "download_catalogue_reference": "下载目录参考" });
const evidence = computed(() => {
  const explicit = presentation.value?.evidence ?? [];
  if (explicit.length) return explicit.map(line => conciseText(Object.entries(fields).reduce((text, [key, label]) => text.replaceAll(key, label), line.replace(/（数据库已核验）/g, ""))));
  const ids = new Set(build.value?.core.map(item => item.candidate_id) ?? []);
  presentation.value?.selections?.forEach(item => ids.add(item.candidate_id));
  return (ids.size ? candidates.value.filter(item => ids.has(item.candidate_id)) : candidates.value.slice(0,1)).flatMap(candidate => candidate.claims.filter(item => item.verification.status === "supported" && item.claim.field_key !== "entity.canonical_name").map(item => {
    const field = item.claim.field_key ?? item.claim.metric_key ?? item.claim.claim_type;
    return `${candidate.canonical_name}：${fields[field] ?? field} ${String(item.verification.canonical_value ?? item.claim.value)} ${item.verification.canonical_unit ?? item.claim.unit ?? ""}`.trim();
  })).slice(0,8);
});
function safeUrl(value: string): boolean { try { return ["http:", "https:"].includes(new URL(value).protocol); } catch { return false; } }
const sources = computed(() => (presentation.value?.sources ?? []).filter(source => safeUrl(source.url)));
</script>
<template>
  <section class="recommendation" aria-label="推荐结果">
    <template v-if="candidates.length">
      <p class="lead">{{ response.answer_purpose === 'explanation' ? '以下是对上次选择的进一步解释：' : isModel ? '以下是为您推荐的模型：' : '以下是为您推荐的硬件：' }}</p>
      <ul class="configuration"><li v-for="(item,index) in configuration" :key="index"><span v-if="item.label">{{ item.label }}：</span><strong class="hardware-name">{{ item.name }}</strong></li></ul>
      <p v-if="presentation?.selection_notice" role="status">{{ presentation.selection_notice }}</p>
      <div class="recommendation__section"><h2>推荐理由：</h2><p>{{ reason }}</p></div>
      <div class="recommendation__section"><h2>证据：</h2>
        <ul v-if="evidence.length"><li v-for="(item,index) in evidence" :key="index">{{ item }}</li></ul>
        <ul v-if="sources.length" class="sources"><li v-for="source in sources" :key="source.url + (source.field ?? '')"><a :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.title || source.url }}</a><span v-if="source.field"> · {{ fields[source.field] ?? source.field }}</span></li></ul>
        <p v-if="!evidence.length && !sources.length">暂无可引用的规格或来源链接。</p>
      </div>
      <p v-if="visibleGaps.length" class="gaps">待补充：{{ visibleGaps.join('；') }}</p>
    </template>
    <div v-else role="status"><p>{{ presentation?.headline ?? naturalLanguage?.overview ?? '本次未找到符合条件的候选，请补充型号或需求。' }}</p></div>
  </section>
</template>
<style scoped>
.recommendation { margin:24px 16px 0; font:15px/1.85 var(--font-sans); overflow-wrap:anywhere; }
p { margin:0; }
.configuration { list-style:none; padding:0; margin:12px 0 24px; color:var(--color-terminal-fg); font-weight:600; }
.configuration li { padding:3px 0; }
.hardware-name { color:#D97757; font-weight:600; }
.recommendation__section { margin-top:24px; }
h2 { margin:0 0 8px; font:600 14px/1.6 var(--font-sans); }
.recommendation__section p, li { color:var(--color-terminal-fg-dim); }
ul { padding-left:20px; margin:8px 0; }
.sources { font:12px/1.8 var(--font-mono); }
a { color:var(--color-status-success); text-underline-offset:3px; }
a:hover { color:var(--color-terminal-fg); }
.gaps { margin-top:16px; color:var(--color-terminal-fg-dim); font-size:13px; }
@media(max-width:760px) { .recommendation { margin-inline:4px; } }
</style>
