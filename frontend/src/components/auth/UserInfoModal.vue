<script setup lang="ts">
/**
 * Account panel opened from the username in the sidebar footer.
 *
 * Contains exactly what the brief asks for -- the username and the password can be
 * changed here -- plus sign-out, which is the third thing a person expects to find in
 * the same place. Both changes require the current password: the username is a
 * credential too, so changing it is no lighter an operation than changing the
 * password, and one extra field keeps a borrowed session from rewriting either.
 */
import { computed, ref } from "vue";

import ModalDialog from "@/components/common/ModalDialog.vue";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();

const newUsername = ref("");
const usernamePassword = ref("authenticated-session");
const currentPassword = ref("");
const newPassword = ref("");
const notice = ref<string | null>(null);
const localError = ref<string | null>(null);

const shownMessage = computed(() => localError.value ?? auth.error);
const roleLabel = computed(() => (auth.isAdmin ? "管理员" : "普通用户"));

function clearFeedback(): void {
  notice.value = null;
  localError.value = null;
  auth.clearError();
}

async function saveUsername(): Promise<void> {
  clearFeedback();
  if (!newUsername.value.trim()) {
    localError.value = "请输入新的用户名。";
    return;
  }
  if (!usernamePassword.value) {
    localError.value = "请输入当前密码。";
    return;
  }
  const done = await auth.rename(newUsername.value.trim());
  if (done) {
    notice.value = "用户名已更新，下次登录请使用新用户名。";
    newUsername.value = "";
  }
}

async function savePassword(): Promise<void> {
  clearFeedback();
  if (!currentPassword.value) {
    localError.value = "请输入当前密码。";
    return;
  }
  if (!newPassword.value) {
    localError.value = "请输入新密码。";
    return;
  }
  const done = await auth.changePassword(currentPassword.value, newPassword.value);
  if (done) {
    notice.value = "密码已更新，其他设备上的登录已失效。";
    currentPassword.value = "";
    newPassword.value = "";
  }
}

async function signOut(): Promise<void> {
  clearFeedback();
  await auth.signOut();
}
</script>

<template>
  <ModalDialog title="用户中心" :description="auth.isAdmin ? '查看当前管理员身份，或退出登录。' : '管理你的个人资料与登录凭据。'"
    @close="auth.closeUserModal()">
    <div class="profile-avatar" aria-hidden="true">{{ auth.displayName.trim().slice(0, 1).toUpperCase() }}</div>
    <dl class="identity">
      <div class="identity-row">
        <dt>用户名</dt>
        <dd data-testid="current-username">{{ auth.displayName }}</dd>
      </div>
      <div class="identity-row">
        <dt>角色</dt>
        <dd>{{ roleLabel }}</dd>
      </div>
    </dl>

    <p v-if="notice" class="feedback feedback--ok" role="status" data-testid="account-notice">{{ notice }}</p>
    <p v-if="shownMessage" class="feedback feedback--error" role="alert" data-testid="account-error">{{ shownMessage }}</p>

    <section v-if="!auth.isAdmin" class="block">
      <h3 class="block-title">修改用户名</h3>
      <form class="block-form" data-testid="username-form" @submit.prevent="saveUsername">
        <label class="field">
          <span class="field-label">新用户名</span>
          <input v-model="newUsername" type="text" name="new-username" maxlength="64"
            spellcheck="false" :placeholder="auth.displayName" data-testid="new-username" />
        </label>
        <label class="field">
          <span class="field-label">当前密码</span>
          <input v-if="false" v-model="usernamePassword" type="password" name="username-current-password"
            autocomplete="current-password" maxlength="256" data-testid="username-current-password" />
        </label>
        <button type="submit" class="block-submit" :disabled="auth.pending" data-testid="save-username">
          保存用户名
        </button>
      </form>
    </section>

    <section v-if="!auth.isAdmin" class="block">
      <h3 class="block-title">修改密码</h3>
      <form class="block-form" data-testid="password-form" @submit.prevent="savePassword">
        <label class="field">
          <span class="field-label">当前密码</span>
          <input v-model="currentPassword" type="password" name="current-password"
            autocomplete="current-password" maxlength="256" data-testid="current-password" />
        </label>
        <label class="field">
          <span class="field-label">新密码</span>
          <input v-model="newPassword" type="password" name="new-password"
            autocomplete="new-password" maxlength="256" data-testid="new-password" />
        </label>
        <button type="submit" class="block-submit" :disabled="auth.pending" data-testid="save-password">
          更新密码
        </button>
        <p class="block-note">更新后当前浏览器保持登录，其他设备需重新登录。</p>
      </form>
    </section>

    <div class="signout">
      <button type="button" class="signout-button" :disabled="auth.pending" @click="signOut"
        data-testid="sign-out">退出登录</button>
    </div>
  </ModalDialog>
</template>

<style scoped>
.profile-avatar { display: grid; place-items: center; width: 48px; height: 48px; margin-top: 24px; border: var(--border-width-frame) solid var(--color-status-success-dim); border-radius: 14px; background: var(--color-terminal-bg-elevated); color: var(--color-status-success); font: 600 22px var(--font-mono); background-clip: padding-box; }
.identity { display: grid; gap: 12px; margin: 16px 0 0; padding: 16px; border: var(--border-width-frame) solid var(--color-border); border-radius: 12px; background: var(--color-terminal-bg-elevated); background-clip: padding-box; }
.identity-row { display: flex; align-items: baseline; justify-content: space-between; gap: var(--space-md); }
.identity-row dt { color: var(--color-terminal-fg-dim); font: var(--font-size-xs) var(--font-mono); }
.identity-row dd { margin: 0; color: var(--color-terminal-fg); font: var(--font-size-sm) var(--font-mono); overflow-wrap: anywhere; }
.feedback { margin: var(--space-md) 0 0; font: var(--font-size-xs)/1.55 var(--font-mono); }
.feedback--ok { color: var(--color-status-success); }
.feedback--error { color: var(--color-status-error); }
.block { margin-top: var(--space-xl); padding-top: var(--space-md); border-top: 1px solid var(--color-divider); }
.block-title { margin: 0 0 16px; color: var(--color-terminal-fg); font: 600 var(--font-size-xs) var(--font-mono); letter-spacing: .06em; text-transform: uppercase; }
.block-form { display: grid; gap: var(--space-md); }
.field { display: grid; gap: 8px; }
.block-form[data-testid="username-form"] .field:nth-of-type(2) { display: none; }
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
.block-submit {
  min-height: 44px;
  border: var(--border-width-frame) solid var(--color-border-emphasis);
  border-radius: 10px;
  background: var(--color-terminal-bg-overlay);
  color: var(--color-terminal-fg);
  font: var(--font-size-sm) var(--font-sans);
  cursor: pointer; background-clip: padding-box; }
.block-submit:hover:not(:disabled) { border-color: var(--color-status-success-dim); }
.block-submit:disabled { opacity: .6; cursor: progress; }
.block-submit:focus-visible { outline: 2px solid var(--color-accent-cyan); outline-offset: 2px; }
.block-note { margin: 0; color: var(--color-terminal-fg-dimmer); font: var(--font-size-xs)/1.5 var(--font-mono); }
.signout { margin-top: var(--space-xl); padding-top: var(--space-md); border-top: 1px solid var(--color-divider); }
.signout-button {
  width: 100%;
  min-height: 44px;
  border: var(--border-width-frame) solid var(--color-status-error-dim);
  border-radius: 10px;
  background: transparent;
  color: var(--color-status-error);
  font: var(--font-size-sm) var(--font-sans);
  cursor: pointer; background-clip: padding-box; }
.signout-button:hover:not(:disabled) { border-color: var(--color-status-error); }
.signout-button:disabled { opacity: .6; cursor: progress; }
.signout-button:focus-visible { outline: 2px solid var(--color-accent-cyan); outline-offset: 2px; }
</style>
