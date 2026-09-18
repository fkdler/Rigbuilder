<script setup lang="ts">
import type { Provenance } from "@/api/adminWorkspace";
defineProps<{ disabled?: boolean }>();
const model = defineModel<Provenance>({ required: true });
</script>
<template>
  <fieldset class="source-fields" :disabled="disabled">
    <legend>修改依据</legend>
    <label>来源标题<input v-model="model.title" required minlength="2" maxlength="255" placeholder="例如：厂商产品规格页" /></label>
    <label>来源链接<input v-model="model.url" type="url" required maxlength="2083" placeholder="https://…" /></label>
    <label class="full">相关原文摘录<textarea v-model="model.excerpt" required minlength="2" maxlength="4000" rows="3" placeholder="粘贴能够支持本次修改的原文；多项修改需覆盖每一项。" /></label>
    <label class="full">修改原因<input v-model="model.reason" required minlength="2" maxlength="1000" placeholder="例如：补充缺失显存信息 / 修正录入错误" /></label>
    <label class="source-check full"><input v-model="model.confirmed" type="checkbox" required />我已核对来源，确认摘录支持本次修改（链接不会自动抓取核验）</label>
  </fieldset>
</template>
<style scoped>
.source-fields { margin: 24px 0 16px; padding: 16px; border: var(--border-width-frame) solid var(--color-border); border-radius: 10px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; background-clip: padding-box; }
legend { padding: 0 6px; color: var(--color-terminal-fg-dim); }
.full { grid-column: 1 / -1; }
.source-check { display: flex; align-items: start; gap: 10px; }
.source-check input { margin-top: 4px; flex-shrink: 0; }
@media (max-width: 760px) { .source-fields { grid-template-columns: 1fr; padding: 12px; } }
</style>
