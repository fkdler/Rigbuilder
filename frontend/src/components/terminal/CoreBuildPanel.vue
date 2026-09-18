<script setup lang="ts">
import type { CoreBuild, SupportBasis } from "@/api/agentFusion";

/**
 * The core build of a whole-machine answer (Plan_V4.2).
 *
 * The Catalogue holds no assembled machine and no compatibility relation, so only the
 * core components are concrete verified rows.  Everything in `supporting` is either a
 * requirement the Release states for the workload or a derivation from the core
 * components, and the badge on each row says which — a supporting part must never read
 * like a verified recommendation.
 */
defineProps<{ build: CoreBuild }>();

const ROLE_LABELS: Record<string, string> = {
  cpu: "CPU",
  gpu: "显卡",
  platform: "平台",
  memory: "内存",
  storage: "存储",
  psu: "电源",
};

const BASIS_LABELS: Record<SupportBasis, string> = {
  database_requirement: "场景需求",
  database_derived: "规则推导",
  not_in_catalogue: "未收录",
};

const roleLabel = (role: string): string => ROLE_LABELS[role] ?? role;
const basisLabel = (basis: SupportBasis): string => BASIS_LABELS[basis] ?? basis;
</script>

<template>
  <section class="core-build" aria-label="核心配置">
    <h3 class="core-build__title">
      <i aria-hidden="true"></i>核心配置
      <span class="core-build__hint">核心硬件为已验证型号，配套仅为规格建议</span>
    </h3>

    <div v-if="build.core.length" class="core-build__core">
      <article v-for="item in build.core" :key="item.candidate_id" class="core-part">
        <span class="core-part__role">{{ roleLabel(item.role) }}</span>
        <h4 class="core-part__name">{{ item.name }}</h4>
      </article>
    </div>

    <dl v-if="build.supporting.length" class="core-build__supporting">
      <div v-for="item in build.supporting" :key="item.role" class="support-row">
        <dt class="support-row__role">{{ roleLabel(item.role) }}</dt>
        <dd class="support-row__body">
          <span class="support-row__value">{{ item.spec }}</span>
          <span class="support-row__basis" :class="`support-row__basis--${item.basis}`">
            {{ basisLabel(item.basis) }}
          </span>
          <span v-if="item.note" class="support-row__note">{{ item.note }}</span>
        </dd>
      </div>
    </dl>

    <ul v-if="build.gaps.length" class="core-build__gaps">
      <li v-for="(gap, index) in build.gaps" :key="index">{{ gap }}</li>
    </ul>
  </section>
</template>

<style scoped>
.core-build {
  display: grid;
  gap: .65rem;
  margin: 0;
  padding: .75rem .85rem;
  border: var(--border-width-frame) solid var(--color-terminal-border, rgba(255, 255, 255, .08));
  border-radius: 3px;
  background: rgba(255, 255, 255, .015); background-clip: padding-box; }

.core-build__title {
  display: flex;
  align-items: baseline;
  gap: .5rem;
  margin: 0;
  color: var(--color-terminal-fg);
  font: 600 .78rem var(--font-mono);
  letter-spacing: .06em;
}
.core-build__title i {
  width: .42rem;
  height: .42rem;
  border-radius: 50%;
  background: var(--color-status-success);
}
.core-build__hint {
  color: var(--color-terminal-fg-dimmer);
  font: .66rem var(--font-mono);
  font-weight: 400;
  letter-spacing: 0;
}

.core-build__core {
  display: grid;
  gap: .5rem;
  grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr));
}

.core-part {
  display: grid;
  gap: .2rem;
  padding: .55rem .65rem;
  border: var(--border-width-frame) solid var(--color-status-success-dim);
  border-radius: 3px;
  background: linear-gradient(180deg, rgba(34, 197, 94, .05), transparent); background-clip: padding-box; }
.core-part__role {
  color: var(--color-status-success);
  font: .62rem var(--font-mono);
  letter-spacing: .08em;
}
.core-part__name {
  margin: 0;
  color: var(--color-terminal-fg);
  font: 600 .92rem/1.4 var(--font-mono);
  overflow-wrap: anywhere;
}

.core-build__supporting {
  display: grid;
  gap: .3rem;
  margin: 0;
}
.support-row {
  display: grid;
  grid-template-columns: 3.2rem 1fr;
  gap: .55rem;
  align-items: baseline;
}
.support-row__role {
  color: var(--color-terminal-fg-dimmer);
  font: .7rem var(--font-mono);
}
.support-row__body { display: flex; flex-wrap: wrap; align-items: baseline; gap: .45rem; margin: 0; }
.support-row__value {
  color: var(--color-terminal-fg-dim);
  font: .78rem/1.6 var(--font-mono);
  overflow-wrap: anywhere;
}
.support-row__basis {
  padding: .05rem .32rem;
  border-radius: 2px;
  font: .6rem var(--font-mono);
  letter-spacing: .04em;
}
.support-row__basis--database_requirement {
  border: var(--border-width-frame) solid var(--color-status-success-dim);
  color: var(--color-status-success); background-clip: padding-box; }
.support-row__basis--database_derived {
  border: var(--border-width-frame) solid var(--color-terminal-fg-dimmer);
  color: var(--color-terminal-fg-dim); background-clip: padding-box; }
.support-row__basis--not_in_catalogue {
  border: var(--border-width-frame) dashed var(--color-terminal-fg-dimmer);
  color: var(--color-terminal-fg-dimmer); background-clip: padding-box; }
.support-row__note {
  color: var(--color-terminal-fg-dimmer);
  font: .66rem/1.6 var(--font-mono);
}

.core-build__gaps {
  display: grid;
  gap: .18rem;
  margin: 0;
  padding: 0;
  list-style: none;
}
.core-build__gaps li {
  color: var(--color-status-warning);
  font: .68rem/1.6 var(--font-mono);
}
.core-build__gaps li::before { content: "! "; color: var(--color-status-warning-dim); }

@media (max-width: 620px) {
  .core-build__core { grid-template-columns: 1fr; }
  .support-row { grid-template-columns: 1fr; gap: .1rem; }
}
</style>
