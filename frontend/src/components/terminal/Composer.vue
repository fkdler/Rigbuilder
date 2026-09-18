<script setup lang="ts">
import { ref } from "vue";

import type { Constraint } from "@/api/agentFusion";
import { useConversationStore } from "@/stores/conversations";
import { useAuthStore } from "@/stores/auth";
import ConstraintPanel from "./ConstraintPanel.vue";

const store = useConversationStore();
const auth = useAuthStore();
const draft = ref("");
const constraints = ref<Constraint[]>([]);
const submitting = ref(false);

async function handleSubmit() {
  const text = draft.value.trim();
  if (!text || submitting.value) return;
  if (!auth.isAuthenticated) {
    auth.openLoginModal();
    return;
  }
  submitting.value = true;
  try {
    // The third argument is the point of this change: `submit` falls back to an
    // empty list when it is omitted, which is what kept `QueryJobRequest.constraints`
    // permanently empty and the backend's verified constraint path unreachable.
    await store.submit(text, store.currentConversationId, constraints.value);
    draft.value = "";
    store.composerText = "";
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <form class="composer" @submit.prevent="handleSubmit">
    <div class="composer__inner">
      <span class="composer__prompt" aria-hidden="true">&gt;</span>
      <label for="rigbuilder-prompt" class="sr-only">向 RigBuilder 提问</label>
      <textarea id="rigbuilder-prompt" v-model="draft" rows="2" placeholder="您可以向 RigBuilder 咨询关于个人 PC 配置的问题。比如：“给我推荐一套打游戏的配置。”" @keydown.ctrl.enter.prevent="handleSubmit" @keydown.meta.enter.prevent="handleSubmit" />
    </div>
    <div class="composer__footer">
      <div class="composer__constraints"><ConstraintPanel v-model="constraints" :disabled="submitting" /></div>
      <span class="composer__hint">Ctrl+Enter</span>
      <button type="submit" :disabled="!draft.trim() || submitting">{{ submitting ? 'sending' : 'RUN ↵' }}</button>
    </div>
  </form>
</template>
<style scoped>
.composer { flex:0 0 auto; width:calc(100% - 56px); max-width:960px; margin:0 auto 24px; padding:14px 16px 6px; border: var(--border-width-frame) solid var(--color-border); border-radius:14px; background:var(--color-terminal-bg-elevated); background-clip: padding-box; }
.composer:focus-within { border-color:var(--color-border-emphasis); }
.composer__inner { display:flex; align-items:baseline; gap:12px; }
.composer__prompt { color:var(--color-status-success); font:16px var(--font-mono); }
textarea { width:100%; min-height:56px; max-height:180px; resize:vertical; border:0; outline:0; background:none; color:var(--color-terminal-fg); font:15px/1.6 var(--font-sans); caret-color:var(--color-status-success); }
textarea::placeholder { color:var(--color-terminal-fg-dim); }
.composer__footer { display:flex; flex-wrap:wrap; align-items:center; gap:12px; }
.composer__constraints { flex:1; min-width:160px; }
.composer__hint { color:var(--color-terminal-fg-dim); font:11px var(--font-mono); }
button { min-height:44px; min-width:60px; border:0; background:none; color:var(--color-status-success); font:600 12px var(--font-mono); cursor:pointer; }
button:hover:not(:disabled) { color:var(--color-terminal-fg); }
button:disabled { opacity:.5; cursor:not-allowed; }
@media(max-width:760px) { .composer { width:calc(100% - 28px); margin-bottom:14px; } .composer__hint { display:none; } }
</style>
