<script setup lang="ts">
/**
 * The final answer: one conclusion, its reasons, the runners-up and the limits.
 *
 * Plan_V4.1. The result area used to open with a ranked list of candidates and a
 * score each; the narration was a paragraph above it. The measured verdict was
 * "没有一个融合的自然语言结果或理由" and "读起来生硬或像罗列" — the answer was
 * technically present but never framed as an answer.
 *
 * This block renders only what the narrator produced. The narrator is forbidden
 * from changing candidates, ranking, scores or facts, so nothing here recomputes
 * or reorders anything from `result`.
 */
import type { PresentationResult } from "@/api/agentFusion";

defineProps<{ presentation: PresentationResult }>();
</script>

<template>
  <section class="presentation" aria-label="最终结论">
    <p class="presentation__headline">{{ presentation.headline }}</p>

    <div v-if="presentation.primary" class="presentation__primary">
      <p class="presentation__pick">
        <span class="presentation__pick-label">推荐</span>
        <span class="presentation__pick-name">{{ presentation.primary.name }}</span>
      </p>
      <ul v-if="presentation.primary.reasons?.length" class="presentation__reasons">
        <li v-for="(reason, index) in presentation.primary.reasons" :key="index">{{ reason }}</li>
      </ul>
    </div>

    <div v-if="presentation.alternatives?.length" class="presentation__group">
      <h4 class="presentation__group-title">备选</h4>
      <p v-for="item in presentation.alternatives" :key="item.candidate_id" class="presentation__alternative">
        <b>{{ item.name }}</b>
        <span>{{ item.note }}</span>
      </p>
    </div>

    <div v-if="presentation.caveats?.length" class="presentation__group">
      <h4 class="presentation__group-title">本结论的局限</h4>
      <ul class="presentation__caveats">
        <li v-for="(caveat, index) in presentation.caveats" :key="index">{{ caveat }}</li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
.presentation {
  display: grid;
  gap: .7rem;
  max-width: 74ch;
  margin: 0;
  padding: 0 0 .2rem;
}

.presentation__headline {
  margin: 0;
  color: var(--color-terminal-fg);
  font-size: .98rem;
  line-height: 1.7;
}

.presentation__primary { display: grid; gap: .4rem; }

.presentation__pick { display: flex; align-items: baseline; gap: .55rem; margin: 0; }
.presentation__pick-label {
  padding: .1rem .38rem;
  border: var(--border-width-frame) solid var(--color-status-success-dim);
  border-radius: 2px;
  color: var(--color-status-success);
  font: .62rem var(--font-mono);
  letter-spacing: .05em; background-clip: padding-box; }
.presentation__pick-name { color: var(--color-terminal-fg); font: 600 1.02rem/1.4 var(--font-mono); overflow-wrap: anywhere; }

.presentation__reasons { display: grid; gap: .35rem; margin: 0; padding: 0 0 0 1.05rem; list-style: none; }
.presentation__reasons li {
  position: relative;
  color: var(--color-terminal-fg-dim);
  font-size: .875rem;
  line-height: 1.7;
}
.presentation__reasons li::before {
  position: absolute;
  left: -1.05rem;
  color: var(--color-status-success-dim);
  content: "·";
}

.presentation__group { display: grid; gap: .3rem; margin-top: .15rem; }
.presentation__group-title {
  margin: 0;
  color: var(--color-terminal-fg-dimmer);
  font: .66rem var(--font-mono);
  letter-spacing: .05em;
}

.presentation__alternative { display: grid; gap: .1rem; margin: 0; font-size: .8rem; line-height: 1.6; }
.presentation__alternative b { color: var(--color-terminal-fg-dim); font-family: var(--font-mono); font-weight: 600; }
.presentation__alternative span { color: var(--color-terminal-fg-dimmer); }

.presentation__caveats { display: grid; gap: .2rem; margin: 0; padding: 0; list-style: none; }
.presentation__caveats li { color: var(--color-status-warning); font: .72rem/1.6 var(--font-mono); }
.presentation__caveats li::before { content: "! "; color: var(--color-status-warning-dim); }

@media (max-width: 620px) {
  .presentation__pick { flex-wrap: wrap; }
}
</style>
