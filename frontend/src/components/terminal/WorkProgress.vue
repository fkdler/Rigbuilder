<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import type { QueryEvent } from "@/api/queryJobs";

const props = defineProps<{
  status: string;
  startedAt: number;
  events?: QueryEvent[];
  // Only real live output/completion usage, never prompt tokens or final-only usage.
  completionTokens?: number | null;
}>();
const frames = ["·", "✢", "✳", "✶", "✻", "✽", "✻", "✶", "✳", "✢"];
const verbs = ["Cooking", "Thinking", "Pondering", "Brewing", "Crafting", "Noodling", "Ruminating", "Synthesizing"];
const frame = ref(0);
const verb = ref(0);
const elapsedSeconds = ref(0);
const active = computed(() => ["submitting", "queued", "running"].includes(props.status));
let animationTimer: ReturnType<typeof setInterval> | undefined;
let elapsedTimer: ReturnType<typeof setInterval> | undefined;
let verbTimer: ReturnType<typeof setInterval> | undefined;
const liveCompletionTokens = computed(() => {
  const activeCalls = new Map<string, number>();
  for (const event of [...(props.events ?? [])].sort((a, b) => a.sequence - b.sequence)) {
    const callId = event.detail?.call_id;
    if (typeof callId !== "string") continue;
    if (event.event_type === "llm_usage_finished") activeCalls.delete(callId);
    else if (event.event_type === "llm_usage") {
      const count = event.detail?.completion_tokens;
      if (typeof count === "number" && Number.isSafeInteger(count) && count > 0) activeCalls.set(callId, count);
    }
  }
  // Measured output tokens of currently generating calls, not whole-job total usage.
  return activeCalls.size ? [...activeCalls.values()].reduce((sum, count) => sum + count, 0) : null;
});
const formattedCompletionTokens = computed(() => {
  const count = props.completionTokens ?? liveCompletionTokens.value;
  if (typeof count !== "number" || !Number.isSafeInteger(count) || count <= 0) return null;
  return count < 1000 ? String(count) : `${Number((count / 1000).toFixed(1))}k`;
});
function stopTimers(): void {
  clearInterval(animationTimer);
  clearInterval(elapsedTimer);
  clearInterval(verbTimer);
  animationTimer = elapsedTimer = verbTimer = undefined;
}
watch(() => [active.value, props.startedAt] as const, () => {
  stopTimers();
  if (!active.value) return;
  frame.value = verb.value = 0;
  const updateElapsed = () => {
    elapsedSeconds.value = Math.max(0, Math.floor((Date.now() - props.startedAt) / 1000));
  };
  updateElapsed();
  elapsedTimer = setInterval(updateElapsed, 1000);
  if (!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
    animationTimer = setInterval(() => { frame.value = (frame.value + 1) % frames.length; }, 80);
    verbTimer = setInterval(() => {
      verb.value = (verb.value + 1 + Math.floor(Math.random() * (verbs.length - 1))) % verbs.length;
    }, 4500);
  } else { frame.value = 4; }
}, { immediate: true });
onBeforeUnmount(stopTimers);
</script>

<template>
  <div v-if="active" class="work-progress" role="status" aria-label="正在处理请求">
    <span class="thinking-spinner" aria-hidden="true">{{ frames[frame] }}</span>
    <span class="thinking-verb" aria-hidden="true">{{ verbs[verb] }}&hellip;</span>
    <span class="thinking-meta" aria-hidden="true">({{ elapsedSeconds }}s<template v-if="formattedCompletionTokens"> &middot; &uarr; {{ formattedCompletionTokens }} tokens</template>)</span>
  </div>
</template>

<style scoped>
.work-progress { display: flex; align-items: baseline; gap: 7px; min-width: 0; font: 12px/1.6 Consolas, "SFMono-Regular", "Cascadia Code", monospace; white-space: nowrap; }
.thinking-spinner { flex: 0 0 1em; width: 1em; text-align: center; color: var(--color-accent); }
.thinking-verb { color: var(--color-terminal-fg-dim); }
.thinking-meta { color: var(--color-terminal-fg-dimmer); font-variant-numeric: tabular-nums; }
</style>
