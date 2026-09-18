<script setup lang="ts">
/**
 * The one dialog shell both account overlays use.
 *
 * Plan V4.5 §11.3 asks for the account UI to live on the conversation page rather
 * than as its own route, so this is an overlay component and not a view: it renders
 * above whatever is already on screen and leaves the router untouched.
 *
 * Keyboard behaviour is implemented here once, for every dialog: Escape closes,
 * Tab cycles within the panel, and focus moves into the panel on open so a keyboard
 * user is not stranded on the page behind it.
 */
import { onBeforeUnmount, onMounted, ref } from "vue";

const props = withDefaults(
  defineProps<{ title: string; description?: string | null; dismissible?: boolean }>(),
  { description: null, dismissible: true },
);
const emit = defineEmits<{ close: [] }>();

const panel = ref<HTMLElement | null>(null);
let previouslyFocused: HTMLElement | null = null;

const FOCUSABLE = [
  "a[href]", "button:not([disabled])", "input:not([disabled])", "select:not([disabled])",
  "textarea:not([disabled])", "[tabindex]:not([tabindex='-1'])",
].join(",");

function focusables(): HTMLElement[] {
  if (!panel.value) return [];
  return Array.from(panel.value.querySelectorAll<HTMLElement>(FOCUSABLE))
    .filter((element) => element.offsetParent !== null || element === document.activeElement);
}

function requestClose(): void {
  if (props.dismissible) emit("close");
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape") {
    event.stopPropagation();
    requestClose();
    return;
  }
  if (event.key !== "Tab") return;
  const items = focusables();
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement as HTMLElement | null;
  if (event.shiftKey && (active === first || !panel.value?.contains(active))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
  }
}

onMounted(() => {
  previouslyFocused = document.activeElement as HTMLElement | null;
  document.addEventListener("keydown", onKeydown, true);
  const target = focusables()[0];
  target?.focus();
});

onBeforeUnmount(() => {
  document.removeEventListener("keydown", onKeydown, true);
  previouslyFocused?.focus?.();
});
</script>

<template>
  <Teleport to="body">
    <div class="modal-overlay" data-testid="modal-overlay" @click.self="requestClose">
      <div ref="panel" class="modal-panel" role="dialog" aria-modal="true" :aria-label="props.title">
        <header class="modal-head">
          <div><p class="modal-eyebrow">&gt; RIGBUILDER / ACCOUNT</p><h2 class="modal-title">{{ props.title }}</h2></div>
          <button v-if="props.dismissible" type="button" class="modal-close" aria-label="关闭"
            @click="emit('close')">✕</button>
        </header>
        <p v-if="props.description" class="modal-description">{{ props.description }}</p>
        <slot />
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.modal-overlay {
  position: fixed;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-lg);
  inset: 0;
  background: rgba(12, 11, 10, 0.72);
  backdrop-filter: blur(8px);
}
.modal-panel {
  width: min(100%, 28rem);
  max-height: calc(100dvh - 2 * var(--space-lg));
  overflow-y: auto;
  padding: clamp(20px, 5vw, 32px);
  border: var(--border-width-frame) solid var(--color-border-emphasis);
  border-radius: 18px;
  background: var(--color-panel);
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.55);
  color: var(--color-terminal-fg); background-clip: padding-box; }
.modal-head { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-md); }
.modal-eyebrow { margin: 0 0 12px; color: var(--color-status-success); font: 10px var(--font-mono); letter-spacing: .12em; }
.modal-title { margin: 0; font: 600 22px/1.4 var(--font-sans); letter-spacing: .02em; }
.modal-close {
  min-width: 32px;
  min-height: 32px;
  border: var(--border-width-frame) solid transparent;
  border-radius: var(--radius-xs);
  background: transparent;
  color: var(--color-terminal-fg-dim);
  font-size: var(--font-size-sm);
  cursor: pointer; background-clip: padding-box; }
.modal-close:hover { border-color: var(--color-border); color: var(--color-terminal-fg); }
.modal-close:focus-visible { outline: 2px solid var(--color-accent-cyan); outline-offset: 2px; }
.modal-description { margin: .6rem 0 0; color: var(--color-terminal-fg-dim); font-size: 13px; line-height: 1.8; }
@media (prefers-reduced-motion: reduce) { * { animation-duration: .01ms !important; transition-duration: .01ms !important; } }
</style>
