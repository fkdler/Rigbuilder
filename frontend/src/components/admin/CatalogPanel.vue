<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { onBeforeRouteLeave, onBeforeRouteUpdate } from "vue-router";
import { adminError, dateTime, getCatalog, getEntity, saveEntity, type CatalogDetail, type CatalogEntity, type EntityEdit } from "@/api/adminWorkspace";
import CatalogMaintenance from "./CatalogMaintenance.vue";

const kind = ref("all");
const query = ref("");
const filter = ref({ kind: "all", q: "" });
const offset = ref(0);
const total = ref(0);
const rows = ref<CatalogEntity[]>([]);
const busy = ref(false);
const saving = ref(false);
const error = ref("");
const message = ref("");
const detail = ref<CatalogDetail | null>(null);
const draft = ref<EntityEdit | null>(null);
const original = ref("");
const maintenanceDirty = ref(false);
const maintenanceSaving = ref(false);
let generation = 0;
const dirty = computed(() => draft.value !== null && JSON.stringify(draft.value) !== original.value);
const statuses: Record<string, string> = { announced: "已公布", upcoming: "即将发布", active: "在售 / 活跃", legacy: "旧代", discontinued: "已停产 / 停更", unknown: "未知" };
const types: Record<string, string> = { hardware: "硬件", ai_model: "模型", model_variant: "模型变体" };
const sections: Record<string, string> = { hardware: "硬件信息", ai_model: "模型信息", model_variant: "模型变体", cpu_spec: "CPU 规格", gpu_spec: "GPU 规格", laptop_gpu_spec: "移动 GPU 规格", datacenter_gpu_spec: "数据中心 GPU 规格", memory_spec: "内存规格", storage_spec: "存储规格", psu_spec: "电源规格", platform_spec: "平台规格", entity_alias: "别名", entity_attribute: "扩展属性", model_capability: "模型能力", price_snapshot: "价格历史" };
const fields: Record<string, string> = { category: "类别", model_name: "型号", product_family: "产品系列", architecture: "架构", socket: "接口", cores_total: "总核心数", threads: "线程数", total_parameters: "总参数量", active_parameters: "激活参数量", context_length_tokens: "上下文长度", license_name: "许可证", family: "模型系列", amount: "价格", currency: "货币", observed_at: "采集时间", unit: "单位", value_status: "数据状态", alias: "别名", capacity_gib: "容量 (GiB)", field_id: "字段 ID", qualifier_key: "限定条件", value_number: "数值", value_integer: "整数值", value_text: "文本值", value_boolean: "布尔值", value_json: "结构化值" };
function display(value: unknown) { return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value); }
function setDraft(entity: CatalogEntity) {
  draft.value = { canonical_name: entity.canonical_name, lifecycle_status: entity.lifecycle_status,
    recommendable: entity.recommendable, release_date: entity.release_date, updated_at: entity.updated_at };
  original.value = JSON.stringify(draft.value);
}
function canLeave() { return !saving.value && !maintenanceSaving.value && (!(dirty.value || maintenanceDirty.value) || window.confirm("有尚未保存的修改，确定放弃吗？")); }
onBeforeRouteLeave(canLeave);
onBeforeRouteUpdate(canLeave);
function unload(event: BeforeUnloadEvent) { if (dirty.value || saving.value || maintenanceDirty.value || maintenanceSaving.value) { event.preventDefault(); event.returnValue = ""; } }
async function load() {
  const request = ++generation; busy.value = true; error.value = "";
  try { const data = await getCatalog(filter.value.kind, filter.value.q, offset.value); if (request === generation) { rows.value = data.items; total.value = data.total; } }
  catch (cause) { if (request === generation) error.value = adminError(cause); }
  finally { if (request === generation) busy.value = false; }
}
function search() { filter.value = { kind: kind.value, q: query.value }; offset.value = 0; void load(); }
function page(delta: number) { offset.value += delta; void load(); }
async function open(entity: CatalogEntity) {
  const request = ++generation; busy.value = true; error.value = ""; message.value = "";
  try { const data = await getEntity(entity.id); if (request === generation) { detail.value = data; setDraft(data.entity); } }
  catch (cause) { if (request === generation) error.value = adminError(cause); }
  finally { if (request === generation) busy.value = false; }
}
function back() { if (!canLeave()) return; detail.value = null; draft.value = null; maintenanceDirty.value = false; error.value = ""; void load(); }
async function refreshAfterMaintenance() {
  if (!detail.value) return;
  try { detail.value = await getEntity(detail.value.entity.id); setDraft(detail.value.entity); }
  catch (cause) { error.value = adminError(cause); }
}
async function save() {
  if (!detail.value || !draft.value || saving.value) return;
  saving.value = true; error.value = ""; message.value = "";
  try {
    const entity = await saveEntity(detail.value.entity.id, { ...draft.value, release_date: draft.value.release_date || null });
    detail.value.entity = entity; setDraft(entity); message.value = "已保存到数据库。";
  } catch (cause) { error.value = adminError(cause); }
  finally { saving.value = false; }
}
onMounted(() => { void load(); window.addEventListener("beforeunload", unload); });
onBeforeUnmount(() => { generation++; window.removeEventListener("beforeunload", unload); });
</script>

<template>
  <section class="admin-page" :aria-busy="busy || saving">
    <header class="admin-heading"><div><h1>{{ detail ? detail.entity.canonical_name : '数据管理' }}</h1><p>{{ detail ? '维护基础信息、关键规格、别名与报价。' : '按实体浏览全部硬件、模型及模型变体。' }}</p></div><button v-if="detail" class="admin-button" :disabled="saving || maintenanceSaving" @click="back">返回列表</button></header>
    <p v-if="message" role="status" class="admin-success">{{ message }}</p>
    <p v-if="error" role="alert" class="admin-error">{{ error }} <button v-if="!detail" class="admin-button" @click="load">重试</button></p>
    <p v-if="busy" role="status" class="admin-muted">正在加载目录数据…</p>
    <template v-if="!detail">
      <form class="admin-toolbar" @submit.prevent="search"><label>数据类型<select v-model="kind"><option value="all">全部类型</option><option value="hardware">硬件</option><option value="model">模型与变体</option></select></label><label class="catalog-search">搜索目录<input v-model="query" type="search" maxlength="100" placeholder="名称或实体标识" /></label><button class="admin-button" :disabled="busy">查询</button></form>
      <template v-if="!busy && !error">
        <div class="admin-table-wrap"><table><caption class="sr-only">硬件与模型目录</caption><thead><tr><th>名称 / 标识</th><th>类型</th><th>生命周期</th><th>可推荐</th><th>操作</th></tr></thead><tbody><tr v-for="entity in rows" :key="entity.id"><td>{{ entity.canonical_name }}<small>{{ entity.entity_key }}</small></td><td>{{ types[entity.entity_type] || entity.entity_type }}</td><td>{{ statuses[entity.lifecycle_status] || entity.lifecycle_status }}</td><td>{{ entity.recommendable ? '是' : '否' }}</td><td><button class="admin-button" :aria-label="`查看与编辑 ${entity.canonical_name}`" @click="open(entity)">查看 / 编辑</button></td></tr></tbody></table></div>
        <p v-if="!rows.length" class="admin-empty">没有匹配的记录，可调整类型或搜索词。</p>
        <footer class="admin-pager"><span>共 {{ total }} 条记录 · 第 {{ Math.floor(offset / 25) + 1 }} 页</span><button class="admin-button" :disabled="offset === 0" @click="page(-25)">上一页</button><button class="admin-button" :disabled="offset + 25 >= total" @click="page(25)">下一页</button></footer>
      </template>
    </template>
    <template v-else-if="draft">
      <p class="admin-muted">{{ types[detail.entity.entity_type] }} · {{ detail.entity.entity_key }}<br />最近修改：{{ dateTime(detail.entity.updated_at) }}</p>
      <form @submit.prevent="save"><fieldset class="catalog-edit" :disabled="maintenanceDirty || maintenanceSaving">
        <label class="wide">名称<input v-model="draft.canonical_name" required maxlength="255" :disabled="saving" /></label>
        <label>生命周期<select v-model="draft.lifecycle_status" :disabled="saving"><option v-for="(label, status) in statuses" :key="status" :value="status">{{ label }}</option></select></label>
        <label>发布日期<input v-model="draft.release_date" type="date" :disabled="saving" /></label>
        <label class="checkbox"><input v-model="draft.recommendable" type="checkbox" :disabled="saving" />允许用于推荐</label>
        <div class="wide edit-actions"><button class="admin-button" :disabled="!dirty || saving || maintenanceDirty || maintenanceSaving">{{ saving ? '保存中…' : '保存修改' }}</button><button class="admin-button" type="button" :disabled="saving || maintenanceDirty || maintenanceSaving" @click="canLeave() && open(detail.entity)">重新读取</button><span class="admin-muted">{{ dirty ? '有未保存的修改' : '已与数据库同步' }}</span></div>
      </fieldset></form>
      <CatalogMaintenance :key="detail.entity.id" :entity="detail.entity" :disabled="dirty || saving || busy" @dirty="maintenanceDirty = $event" @saving="maintenanceSaving = $event" @saved="refreshAfterMaintenance" />
      <div class="admin-section-title"><h2>原始规格与关联数据</h2><span>只读查看</span></div>
      <p class="admin-muted">完整字段供核对使用。日常修改请使用上方维护表单；评测、估算值和实体关系不在此处直接修改。</p>
      <p v-if="!detail.sections.length" class="admin-empty">该实体暂时没有关联数据。</p>
      <details v-for="section in detail.sections" :key="section.table" class="catalog-section"><summary>{{ sections[section.table] || section.table }} · {{ section.total }} 条</summary>
        <p v-if="section.total > section.rows.length" class="admin-notice">共 {{ section.total }} 条，当前展示前 {{ section.rows.length }} 条。</p>
        <div v-for="(row, index) in section.rows" :key="index" class="admin-table-wrap"><table><caption>{{ sections[section.table] || section.table }} {{ section.rows.length > 1 ? `· ${index + 1}` : '' }}</caption><tbody><tr v-for="(value, key) in row" :key="key"><th scope="row">{{ fields[String(key)] || key }}</th><td class="field-value">{{ display(value) }}</td></tr></tbody></table></div>
      </details>
    </template>
  </section>
</template>

<style scoped>
.catalog-search { flex: 1; max-width: 360px; }
.admin-heading h1 { overflow-wrap: anywhere; }
.admin-heading .admin-button { flex-shrink: 0; }
.catalog-edit { border: 0; padding: 0; min-width: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; max-width: 720px; margin: 28px 0 40px; }
.wide { grid-column: 1 / -1; }
.checkbox { display: flex; align-items: center; gap: 10px; min-height: 44px; }
.checkbox input { width: 17px; height: 17px; accent-color: var(--color-accent-cyan); }
.edit-actions { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
.catalog-section { border-top: 1px solid var(--color-border); }
.catalog-section th { width: 34%; white-space: normal; overflow-wrap: anywhere; }
.field-value { white-space: pre-wrap; }
caption { text-align: left; padding: 10px 12px; color: var(--color-terminal-fg-dim); }
</style>
