<script setup lang="ts">
/**
 * Sign-in / sign-up, as an overlay on the conversation page.
 *
 * There is deliberately no `/login` route: the brief for this batch is that the
 * account gate is a modal over the terminal, so a signed-out visit still shows the
 * product behind it and no navigation state has to be restored after signing in.
 *
 * Opened on demand; dismissing the dialog preserves the conversation draft.
 */
import { computed, ref, watch } from "vue";

import ModalDialog from "@/components/common/ModalDialog.vue";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();

const mode = ref<"login" | "register">("login");
const username = ref("");
const password = ref("");
const localError = ref<string | null>(null);

const isRegister = computed(() => mode.value === "register");
const submitLabel = computed(() => (isRegister.value ? "创建账号并登录" : "登录"));
const shownError = computed(() => localError.value ?? auth.error);

watch(mode, () => {
  localError.value = null;
  auth.clearError();
});

function switchMode(): void {
  mode.value = isRegister.value ? "login" : "register";
}

async function submit(): Promise<void> {
  localError.value = null;
  // Only the two rules the server also enforces are checked here, so a click on an
  // empty form produces an inline hint instead of a round trip. There is no
  // password-strength policy on purpose (this batch sets none).
  if (!username.value.trim()) {
    localError.value = "请输入用户名。";
    return;
  }
  if (!password.value) {
    localError.value = "请输入密码。";
    return;
  }
  const done = isRegister.value
    ? await auth.signUp(username.value.trim(), password.value)
    : await auth.signIn(username.value.trim(), password.value);
  if (done) password.value = "";
}
</script>

<template>
  <ModalDialog :title="isRegister ? '创建你的账号' : '登录 RigBuilder'"
    description="让每一次装机灵感，都有迹可循。登录后开始对话，继续你的配置探索。"
    @close="auth.closeLoginModal()">
    <div class="mode-switch" role="tablist" aria-label="登录或注册">
      <button type="button" role="tab" :aria-selected="!isRegister" class="mode-tab"
        :class="{ 'mode-tab--active': !isRegister }" @click="mode = 'login'">登录</button>
      <button type="button" role="tab" :aria-selected="isRegister" class="mode-tab"
        :class="{ 'mode-tab--active': isRegister }" @click="mode = 'register'">注册</button>
    </div>

    <form class="auth-form" @submit.prevent="submit">
      <label class="field">
        <span class="field-label">用户名</span>
        <input v-model="username" type="text" name="username" autocomplete="username"
          placeholder="输入用户名" maxlength="64" spellcheck="false" data-testid="auth-username" />
      </label>
      <label class="field">
        <span class="field-label">密码</span>
        <input v-model="password" type="password" name="password"
          :autocomplete="isRegister ? 'new-password' : 'current-password'"
          placeholder="输入密码" maxlength="256" data-testid="auth-password" />
      </label>

      <p v-if="shownError" class="auth-error" role="alert" data-testid="auth-error">{{ shownError }}</p>

      <button type="submit" class="auth-submit" :disabled="auth.pending" data-testid="auth-submit">
        {{ auth.pending ? "处理中…" : submitLabel }}
      </button>
    </form>

    <p class="auth-foot">
      <button type="button" class="link-button" @click="switchMode">
        {{ isRegister ? "已有账号？去登录" : "没有账号？创建一个" }}
      </button>
    </p>
  </ModalDialog>
</template>

<style scoped>
.mode-switch { display: flex; gap: 4px; padding: 4px; border: var(--border-width-frame) solid var(--color-border); border-radius: 12px; background: var(--color-terminal-bg); margin: 24px 0; background-clip: padding-box; }
.mode-tab {
  flex: 1;
  min-height: 36px;
  border: var(--border-width-frame) solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--color-terminal-fg-dim);
  font: var(--font-size-sm) var(--font-mono);
  cursor: pointer; background-clip: padding-box; }
.mode-tab:hover { border-color: var(--color-border-emphasis); color: var(--color-terminal-fg); }
.mode-tab--active { border-color: var(--color-status-success-dim); color: var(--color-terminal-fg); background: var(--color-terminal-bg-overlay); }
.mode-tab:focus-visible { outline: 2px solid var(--color-accent-cyan); outline-offset: 2px; }
.auth-form { display: grid; gap: 18px; }
.field { display: grid; gap: 8px; }
.field-label { color: var(--color-terminal-fg-dim); font: var(--font-size-xs) var(--font-mono); }
.field input {
  min-height: 44px;
  padding: 0 12px;
  border: var(--border-width-frame) solid var(--color-border);
  border-radius: 10px;
  background: var(--color-terminal-bg);
  color: var(--color-terminal-fg);
  font: var(--font-size-sm) var(--font-mono); background-clip: padding-box; }
.field input:focus-visible { outline: 2px solid var(--color-accent-cyan); outline-offset: 1px; border-color: var(--color-border-emphasis); }
.auth-error { margin: 0; color: var(--color-status-error); font: var(--font-size-xs)/1.55 var(--font-mono); }
.auth-submit {
  min-height: 44px;
  border: var(--border-width-frame) solid var(--color-status-success-dim);
  border-radius: 10px;
  background: var(--color-status-success);
  color: var(--color-terminal-bg);
  font: 600 var(--font-size-sm) var(--font-sans);
  cursor: pointer; background-clip: padding-box; }
.auth-submit:hover:not(:disabled) { border-color: var(--color-status-success); }
.auth-submit:disabled { opacity: .6; cursor: progress; }
.auth-submit:focus-visible { outline: 2px solid var(--color-accent-cyan); outline-offset: 2px; }
.auth-foot { margin: var(--space-md) 0 0; text-align: center; }
.link-button {
  min-height: 32px;
  border: 0;
  background: transparent;
  color: var(--color-accent-cyan);
  font: var(--font-size-xs) var(--font-mono);
  text-decoration: underline;
  text-underline-offset: .22em;
  cursor: pointer;
}
.link-button:focus-visible { outline: 2px solid var(--color-accent-cyan); outline-offset: 2px; }
</style>
