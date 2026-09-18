<script setup lang="ts">
import { MISSING } from "./format";

withDefaults(defineProps<{
  label: string;
  value: string | number | null;
  /** Secondary line, e.g. "p50 86.1 s · p95 154.0 s". */
  detail?: string | null;
  /** When set, states how many runs the metric actually covers. */
  coverage?: string | null;
  tone?: "default" | "success" | "warning" | "error";
}>(), { detail: null, coverage: null, tone: "default" });

const display = (value: string | number | null) => (value === null ? MISSING : String(value));
</script>

<template>
  <div class="metric-card" :data-tone="tone">
    <span class="metric-card__label">{{ label }}</span>
    <span class="metric-card__value">{{ display(value) }}</span>
    <span v-if="detail" class="metric-card__detail">{{ detail }}</span>
    <span v-if="coverage" class="metric-card__coverage">{{ coverage }}</span>
  </div>
</template>

<style scoped>
.metric-card { display: grid; gap: .2rem; padding: .7rem .85rem; border: var(--border-width-frame) solid var(--color-border); border-radius: 2px; background: var(--color-terminal-bg-elevated); background-clip: padding-box; }
.metric-card__label { color: var(--color-terminal-fg-dimmer); font: .68rem var(--font-mono); letter-spacing: .02em; }
.metric-card__value { color: var(--color-terminal-fg); font: 600 1.15rem/1.2 var(--font-mono); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.metric-card__detail { color: var(--color-terminal-fg-dim); font: .68rem var(--font-mono); font-variant-numeric: tabular-nums; }
.metric-card__coverage { color: var(--color-terminal-fg-dimmer); font: .64rem var(--font-mono); }
.metric-card[data-tone="success"] .metric-card__value { color: var(--color-status-success); }
.metric-card[data-tone="warning"] .metric-card__value { color: var(--color-status-warning); }
.metric-card[data-tone="error"] .metric-card__value { color: var(--color-status-error); }
</style>
