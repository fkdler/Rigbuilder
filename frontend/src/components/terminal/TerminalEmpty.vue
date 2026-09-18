<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from "vue";
const text = ref("RigBuilder");
const waiting = ref(true);
let timer: ReturnType<typeof setTimeout> | undefined;
let media: MediaQueryList | undefined;
let phrase = 0;
let deleting = true;
const phrases = ["RigBuilder", "Your PC Part Picker"];
function tick() {
  const target = phrases[phrase]!;
  waiting.value = false;
  text.value = deleting ? text.value.slice(0, -1) : target.slice(0, text.value.length + 1);
  let delay = deleting ? 45 + Math.random() * 20 : 80 + Math.random() * 30;
  if (!text.value && deleting) { deleting = false; phrase = (phrase + 1) % phrases.length; delay = 350; waiting.value = true; }
  else if (text.value === target && !deleting) { deleting = true; delay = 1000; waiting.value = true; }
  timer = setTimeout(tick, delay);
}
function reset() { clearTimeout(timer); text.value = "RigBuilder"; phrase = 0; deleting = true; waiting.value = true; if (!media?.matches) timer = setTimeout(tick, 1000); }
onMounted(() => { media = window.matchMedia("(prefers-reduced-motion: reduce)"); media.addEventListener("change", reset); reset(); });
onBeforeUnmount(() => { clearTimeout(timer); media?.removeEventListener("change", reset); });
</script>
<template><div class="terminal-empty" aria-label="RigBuilder — Your PC Part Picker"><p aria-hidden="true">{{ text }}<span class="cursor" :class="{ waiting }">█</span></p></div></template>
<style scoped>
.terminal-empty { display:grid; place-items:center; height:clamp(170px, 48vh, 560px); }
p { margin:0; color:var(--color-accent); font:clamp(20px, 2vw, 24px)/1.5 var(--font-mono); }
.cursor { color:var(--color-status-success); }
.waiting { animation:blink 560ms steps(1) infinite; }
@keyframes blink { 50% { opacity:.25; } }
@media(prefers-reduced-motion:reduce) { .cursor { animation:none; } }
</style>
