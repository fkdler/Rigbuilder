<script setup lang="ts">
import { computed } from "vue";
import type { FusedClaim } from "@/api/agentFusion";

const props = defineProps<{ claims: FusedClaim[] }>();

interface FactRow {
  label: string;
  value: string;
  status: "supported" | "partial" | "conflict" | "missing";
  evidenceCount: number;
}

const FIELD_LABELS: Record<string, string> = {
  "gpu.vram_gib": "VRAM",
  "gpu.board_power_w": "POWER",
  "gpu.architecture": "ARCH",
  "gpu.memory_type": "MEM",
  "price": "PRICE",
};

const rows = computed<FactRow[]>(() => {
  return props.claims.map(({ verification: cv }): FactRow => {
      const fk = cv.claim.field_key ?? cv.claim.claim_type;
      const label = FIELD_LABELS[fk] ?? fk.replace("gpu.", "").toUpperCase();
      const value = cv.canonical_value != null ? String(cv.canonical_value) : String(cv.claim.value ?? "?");
      const unit = cv.canonical_unit ?? cv.claim.unit ?? "";
      const status = cv.status === "supported" ? "supported" : cv.status === "conflict" ? "conflict" : cv.status === "missing" ? "missing" : "partial";
      const evidenceCount = cv.valid_evidence_ids.length + (cv.valid_price_snapshot_ids?.length ?? 0);
      return { label, value: unit ? `${value} ${unit}` : value, status, evidenceCount };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
});

const statusText = (s: FactRow["status"]) =>
  s === "supported" ? "VERIFIED" : s === "conflict" ? "CONFLICT" : s === "missing" ? "MISSING" : "PARTIAL";

const statusColor = (s: FactRow["status"]) =>
  `fact-state--${s}`;
</script>

<template>
  <dl v-if="rows.length" class="facts" aria-label="已验证事实">
    <div v-for="row in rows" :key="row.label" class="fact-row">
      <dt>{{ row.label }}</dt>
      <dd class="fact-value">{{ row.value }}</dd>
      <dd :class="statusColor(row.status)" class="fact-state">
        {{ statusText(row.status) }}
      </dd>
    </div>
  </dl>
</template>

<style scoped>
.facts { display: grid; gap: .3rem; width: min(100%, 58ch); margin: 1rem 0 0; font: .76rem/1.45 var(--font-mono); }
.fact-row { display: grid; grid-template-columns: 5.25rem minmax(0, 1fr) auto; align-items: baseline; gap: 1rem; min-height: 24px; }
.fact-row dt, .fact-row dd { margin: 0; }
.fact-row dt { color: var(--color-terminal-fg-dimmer); font-size: .67rem; letter-spacing: .06em; }
.fact-value { overflow-wrap: anywhere; color: var(--color-terminal-fg); font-variant-numeric: tabular-nums; }
.fact-state { font-size: .65rem; font-weight: 600; letter-spacing: .04em; }
.fact-state--supported { color: var(--color-status-success); }
.fact-state--conflict, .fact-state--missing { color: var(--color-status-error); }
.fact-state--partial { color: var(--color-status-warning); }
@media (max-width: 520px) { .fact-row { grid-template-columns: 4.2rem minmax(0, 1fr) auto; gap: .65rem; } }
</style>
