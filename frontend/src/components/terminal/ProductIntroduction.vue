<script setup lang="ts">
import { computed } from "vue";
import type { ProductInfoResult } from "@/api/queryJobs";
const props = defineProps<{ result: ProductInfoResult }>();
const introduction = computed(() => props.result.answer.replace('部分规格尚缺字段级证据；下方来源注明了实际支持的内容。', ''));
const sources = computed(() => props.result.sources.filter(source => {
  try { return ["https:", "http:"].includes(new URL(source.url).protocol); } catch { return false; }
}));
</script>
<template>
  <section class="product-introduction" aria-label="产品介绍">
    <p>{{ introduction }}</p>
    <dl v-if="result.facts?.length" class="product-facts">
      <div v-for="fact in result.facts" :key="fact.field">
        <dt>{{ fact.label }}</dt>
        <dd>{{ fact.value }} {{ fact.unit }}</dd>
      </div>
    </dl>
    <ul v-if="sources.length" aria-label="资料来源">
      <li v-for="source in sources" :key="source.url + (source.field ?? '')"><a :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.title || source.url }}</a><span v-if="source.label"> · {{ source.label }}</span></li>
    </ul>
    <details v-if="result.facts?.length" class="verification-details">
      <summary>资料核对详情</summary>
      <ul><li v-for="fact in result.facts" :key="fact.field">{{ fact.label }}：{{ fact.status === 'catalogue_only' ? '目录收录 · 待补证据' : '已核验' }}</li></ul>
    </details>
  </section>
</template>
<style scoped>
.product-introduction { margin:24px 16px; font:15px/1.85 var(--font-sans); overflow-wrap:anywhere; }
p { margin:0; white-space:pre-wrap; }
.product-facts { margin:16px 0; }
.product-facts > div { display:grid; grid-template-columns:7em minmax(0,1fr); gap:12px; padding:6px 0; }
dt { color:var(--color-terminal-fg-dim); }
dd { margin:0; }
.verification-details { margin-top:12px; font-size:12px; color:var(--color-terminal-fg-dim); }
summary { cursor:pointer; padding-block:6px; }
ul { padding-left:20px; margin:16px 0 0; font-size:13px; }
a { color:var(--color-accent); text-underline-offset:3px; }
@media(max-width:760px) { .product-introduction { margin-inline:4px; } }
</style>
