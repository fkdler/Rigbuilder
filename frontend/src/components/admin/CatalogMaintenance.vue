<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { adminError, appendQuote, dateTime, getMaintenance, saveMaintenance, type CatalogEntity, type MaintenanceState, type Provenance } from "@/api/adminWorkspace";
import SourceFields from "./SourceFields.vue";

const props = defineProps<{ entity: CatalogEntity; disabled: boolean }>();
const emit = defineEmits<{ dirty: [value: boolean]; saving: [value: boolean]; saved: [] }>();
const state = ref<MaintenanceState | null>(null);
const values = ref<Record<string, string>>({});
const aliases = ref("");
const error = ref("");
const message = ref("");
const busy = ref(false);
const saving = ref(false);
const onlyMissing = ref(false);
const quoteOpen = ref(false);
const emptySource = (): Provenance => ({ title: "", url: "", excerpt: "", reason: "", confirmed: false });
const source = ref(emptySource());
const quoteSource = ref(emptySource());
const quote = ref({ amount: "", currency: "CNY", market_region: "CN", condition: "new", observed_at: "" });
let active = true;
const serialize = (v: unknown) => v === null ? "" : String(v);
const changedFields = computed(() => state.value?.fields.filter(f => values.value[f.key] !== serialize(f.value)) ?? []);
const aliasesList = computed(() => aliases.value.split(/\r?\n/).map(v => v.trim()).filter(Boolean));
const aliasesChanged = computed(() => !!state.value && JSON.stringify(aliasesList.value) !== JSON.stringify(state.value.aliases));
const factsChanged = computed(() => changedFields.value.length > 0 || aliasesChanged.value);
const quoteDirty = computed(() => !!quote.value.amount || !!quote.value.observed_at || Object.values(quoteSource.value).some(Boolean));
const sourceDirty = computed(() => Object.values(source.value).some(Boolean));
const dirty = computed(() => factsChanged.value || sourceDirty.value || quoteDirty.value);
const blocked = computed(() => props.disabled || busy.value || saving.value);
const visibleFields = computed(() => state.value?.fields.filter(f => !onlyMissing.value || f.missing) ?? []);
const actionLabels: Record<string, string> = { maintain_catalog: "规格 / 别名维护", append_quote: "新增报价", update_catalog: "基础信息修改" };
watch(dirty, value => emit("dirty", value));
watch(saving, value => emit("saving", value));
watch(() => props.entity.updated_at, () => { if (!dirty.value && !saving.value) void load(); });
function accept(data: MaintenanceState) {
  state.value = data;
  values.value = Object.fromEntries(data.fields.map(f => [f.key, serialize(f.value)]));
  aliases.value = data.aliases.join("\n"); source.value = emptySource();
}
async function load() {
  if (dirty.value && !window.confirm("重新读取会放弃当前维护草稿，确定继续吗？")) return;
  busy.value = true; error.value = "";
  try { const data = await getMaintenance(props.entity.id); if (active) { accept(data); resetQuote(); } }
  catch (cause) { if (active) error.value = adminError(cause); }
  finally { if (active) busy.value = false; }
}
function resetQuote() { quote.value = { amount: "", currency: "CNY", market_region: "CN", condition: "new", observed_at: "" }; quoteSource.value = emptySource(); }
async function save() {
  if (!state.value || blocked.value || !factsChanged.value) return;
  const changes: Record<string, string | boolean | null> = {};
  for (const field of changedFields.value) {
    const value = values.value[field.key]?.trim() ?? "";
    changes[field.key] = value === "" ? null : field.kind === "boolean" ? value === "true" : value;
  }
  saving.value = true; error.value = ""; message.value = "";
  try {
    const data = await saveMaintenance(props.entity.id, { revision: state.value.revision, values: changes,
      ...(aliasesChanged.value ? { aliases: aliasesList.value } : {}),
      ...(changedFields.value.length ? { provenance: source.value } : {}),
    });
    accept(data); message.value = "规格与别名已保存，修改记录已更新。"; emit("saved");
  } catch (cause) { error.value = adminError(cause); }
  finally { saving.value = false; }
}
async function addPrice() {
  if (!state.value || blocked.value || factsChanged.value || sourceDirty.value) return;
  saving.value = true; error.value = ""; message.value = "";
  try {
    const data = await appendQuote(props.entity.id, { revision: state.value.revision, ...quote.value,
      observed_at: `${quote.value.observed_at}:00+08:00`, provenance: quoteSource.value });
    accept(data); resetQuote(); quoteOpen.value = false;
    message.value = "新报价已追加，历史价格已保留。"; emit("saved");
  } catch (cause) { error.value = adminError(cause); }
  finally { saving.value = false; }
}
onMounted(load);
onBeforeUnmount(() => { active = false; });
</script>
<template>
  <section class="maintenance" aria-label="关键数据维护" :aria-busy="busy || saving">
    <div class="admin-section-title"><h2>关键数据维护</h2><button type="button" class="admin-button" :disabled="blocked" @click="load">刷新维护数据</button></div>
    <p class="admin-muted">优先补齐影响选型的规格；空值表示未知，不等于 0。每次修改保留依据，旧证据内容不会被覆盖。</p>
    <p v-if="props.disabled" class="admin-notice">请先保存或撤销上方基础信息的修改，再维护关键数据。</p>
    <p v-if="error" role="alert" class="admin-error">{{ error }} <button v-if="!state" type="button" class="admin-button" :disabled="busy" @click="load">重试维护接口</button></p>
    <p v-if="message" role="status" class="admin-success">{{ message }}</p>
    <p v-if="busy" class="admin-muted" role="status">正在读取可维护字段…</p>
    <template v-if="state">
      <form @submit.prevent="save">
        <fieldset :disabled="blocked" class="plain-fieldset">
          <div v-if="state.fields.length" class="field-filter"><label><input v-model="onlyMissing" type="checkbox" />只看待补充项</label><span class="admin-muted">{{ state.fields.filter(f => f.missing).length }} 项未填写 / {{ state.fields.length }} 项可维护</span></div>
          <p v-else class="admin-muted">此类型暂不开放规格纠错，可维护别名{{ entity.entity_type === 'hardware' ? '和报价' : '' }}。量化关系、自动估算与评测记录保留为只读。</p>
          <div class="spec-grid">
            <label v-for="field in visibleFields" :key="field.key">
              <span>{{ field.label }}<small v-if="field.unit"> · {{ field.unit }}</small></span>
              <select v-if="field.kind === 'boolean'" v-model="values[field.key]"><option value="">未知</option><option value="true">支持</option><option value="false">不支持</option></select>
              <input v-else v-model="values[field.key]" type="text" :inputmode="field.kind === 'text' ? 'text' : field.kind === 'integer' ? 'numeric' : 'decimal'" :maxlength="field.kind === 'text' ? field.maximum : 40" placeholder="未知，待补充" />
            </label>
          </div>
          <label class="alias-field">别名（每行一个，最多 100 个）<textarea v-model="aliases" rows="4" placeholder="例如：RTX 5090\nGeForce RTX 5090" /><small>帮助识别简称、中文名和不同写法。删除某一行会移除该别名。</small></label>
          <div v-if="factsChanged" class="change-preview">
            <h3>本次变更</h3>
            <ul><li v-for="field in changedFields" :key="field.key">{{ field.label }}：<del>{{ serialize(field.value) || '未知' }}</del> → <strong>{{ values[field.key] || '未知（清空）' }}</strong></li><li v-if="aliasesChanged">别名由 {{ state.aliases.length }} 项更新为 {{ aliasesList.length }} 项</li></ul>
          </div>
          <SourceFields v-if="changedFields.length" v-model="source" :disabled="blocked" />
          <button class="admin-button" :disabled="blocked || !factsChanged">{{ saving ? '保存中…' : '保存关键数据' }}</button>
        </fieldset>
      </form>
      <section v-if="entity.entity_type === 'hardware'" class="quotes">
        <div class="admin-section-title"><h2>硬件报价</h2><button type="button" class="admin-button" :disabled="blocked" @click="quoteOpen = !quoteOpen">{{ quoteOpen ? '收起报价表单' : '新增报价' }}</button></div>
        <p class="admin-muted">新增带来源和采集时间的购买价格，保留所有历史报价。不同地区、币种、新旧状态分别记录。</p>
        <div v-if="state.prices.length" class="admin-table-wrap"><table><caption class="sr-only">最近五条报价</caption><thead><tr><th>金额</th><th>市场 / 状态</th><th>采集时间</th></tr></thead><tbody><tr v-for="(price, i) in state.prices" :key="i"><td>{{ price.amount }} {{ price.currency }}</td><td>{{ price.market_region }} / {{ price.condition === 'new' ? '全新' : '二手' }}</td><td>{{ dateTime(price.observed_at) }}</td></tr></tbody></table></div>
        <p v-else class="admin-muted">暂无报价，可补充第一条有来源的价格。</p>
        <form v-if="quoteOpen" @submit.prevent="addPrice">
          <fieldset class="plain-fieldset" :disabled="blocked">
            <p v-if="factsChanged || sourceDirty" class="admin-notice">请先保存或撤销上方规格与别名草稿，再新增报价。</p>
            <div class="spec-grid"><label>报价金额<input v-model="quote.amount" required type="text" inputmode="decimal" maxlength="20" /></label><label>币种<select v-model="quote.currency"><option value="CNY">人民币 CNY</option><option value="USD">美元 USD</option></select></label><label>市场<select v-model="quote.market_region"><option value="CN">中国大陆</option><option value="US">美国</option><option value="GLOBAL">全球</option></select></label><label>商品状态<select v-model="quote.condition"><option value="new">全新</option><option value="used">二手</option></select></label><label>采集时间（北京时间）<input v-model="quote.observed_at" type="datetime-local" required /></label></div>
            <SourceFields v-model="quoteSource" :disabled="blocked" />
            <button class="admin-button" :disabled="blocked || factsChanged || sourceDirty">追加报价</button>
          </fieldset>
        </form>
      </section>
      <details class="change-history"><summary>最近修改记录 · {{ state.changes.length }} 条</summary><p v-if="!state.changes.length" class="admin-muted">尚无在线修改记录。</p><article v-for="change in state.changes" :key="change.id"><h3>{{ actionLabels[change.action] || change.action }}</h3><time>{{ dateTime(change.created_at) }}</time><details><summary>查看修改前后及依据</summary><h4>修改前</h4><pre>{{ JSON.stringify(change.before, null, 2) }}</pre><h4>修改后</h4><pre>{{ JSON.stringify(change.after, null, 2) }}</pre></details></article></details>
      <p class="admin-muted">在线修改立即写入数据库；后续导入版本可能覆盖这些值，修改记录用于核对与重新应用。</p>
    </template>
  </section>
</template>
<style scoped>
.maintenance { border-top: 1px solid var(--color-border); margin-top: 32px; padding-top: 8px; }
.plain-fieldset { border: 0; margin: 0; padding: 0; min-width: 0; }
.spec-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px 20px; margin: 20px 0; }
.field-filter { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 12px; margin-top: 20px; }
.field-filter label { display: flex; gap: 8px; align-items: center; }
.alias-field { margin: 24px 0; }
.alias-field small { color: var(--color-terminal-fg-dimmer); }
.change-preview { padding: 12px 0; border-block: 1px solid var(--color-border); margin: 20px 0; }
h3 { font-size: 14px; margin: 8px 0; } h4 { font-size: 13px; }
li { overflow-wrap: anywhere; margin: 6px 0; } del { color: var(--color-terminal-fg-dim); }
.quotes { margin-top: 36px; }
.change-history { margin: 32px 0; }
article { border-top: 1px solid var(--color-border); padding: 12px 0; }
time { font-size: 12px; color: var(--color-terminal-fg-dimmer); }
pre { font-size: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
@media (max-width: 760px) { .spec-grid { grid-template-columns: 1fr; } }
</style>
