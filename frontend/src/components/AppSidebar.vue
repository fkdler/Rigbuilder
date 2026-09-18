<script setup lang="ts">
import { computed, onMounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import AsciiLogoLarge from "@/components/AsciiLogoLarge.vue";
import { useAuthStore } from "@/stores/auth";
import { useConversationStore } from "@/stores/conversations";

const props = withDefaults(defineProps<{ admin?: boolean }>(), { admin: false });
const store = useConversationStore();
const auth = useAuthStore();
const route = useRoute();
const router = useRouter();

function loadIfSignedIn(): void {
  if (auth.isAuthenticated && !props.admin) void store.loadConversations();
}

onMounted(loadIfSignedIn);
watch(() => auth.isAuthenticated, loadIfSignedIn);

const activeModule = computed(() => route.query.module === "daily" ? "daily" : "terminal");
const initial = computed(() => (auth.displayName.trim()[0] ?? "?").toUpperCase());

function formatDate(iso: string): string {
  const delta = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(delta / 60_000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 1_440) return `${Math.floor(minutes / 60)} 小时前`;
  return new Date(iso).toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
}

function statusLabel(status?: string | null): string {
  if (["running", "queued", "cancel_requested"].includes(status ?? "")) return "running";
  if (status === "failed") return "failed";
  if (status === "completed") return "done";
  return "idle";
}

async function remove(id: string, event: Event): Promise<void> {
  event.stopPropagation();
  try { await store.removeConversation(id); } catch { /* rendered in the workspace */ }
}

function openNewTerminal(): void {
  store.createNewSession();
  void router.push({ path: "/", query: { module: "terminal" } });
}

function openDaily(): void {
  void router.push({ path: "/", query: { module: "daily" } });
}

async function openConversation(id: string): Promise<void> {
  await store.selectConversation(id);
  await router.push({ path: "/", query: { module: "terminal", conversation: id } });
}
</script>

<template>
  <aside class="sidebar" :class="{ 'sidebar--admin': admin }" :aria-label="admin ? 'RigBuilder 后台' : 'RigBuilder 工作区'">
    <div class="sidebar-logo">
      <AsciiLogoLarge />
      <pre v-if="admin" class="admin-art" aria-label="ADMIN"> █████╗ ██████╗ ███╗   ███╗██╗███╗   ██╗
██╔══██╗██╔══██╗████╗ ████║██║████╗  ██║
███████║██║  ██║██╔████╔██║██║██╔██╗ ██║
██╔══██║██║  ██║██║╚██╔╝██║██║██║╚██╗██║
██║  ██║██████╔╝██║ ╚═╝ ██║██║██║ ╚████║
╚═╝  ╚═╝╚═════╝ ╚═╝     ╚═╝╚═╝╚═╝  ╚═══╝</pre>
    </div>
    <slot v-if="admin" name="admin-navigation" />
    <template v-else>
      <nav class="module-nav" aria-label="主模块">
        <button type="button" :aria-current="activeModule === 'terminal' ? 'page' : undefined" @click="openNewTerminal">
          <span>会话终端</span><small>决策终端 · 新建会话</small>
        </button>
        <button type="button" :aria-current="activeModule === 'daily' ? 'page' : undefined" @click="openDaily">
          <span>硬件日报</span><small>查看硬件新闻</small>
        </button>
      </nav>
      <div class="section-header"><h2>会话</h2></div>
      <div class="session-scrollarea">
        <p v-if="!store.conversations.length" class="session-empty">暂无会话</p>
        <ul v-else class="session-list">
          <li v-for="conversation in store.conversations" :key="conversation.id" class="session-item" :class="{ 'session-item--active': conversation.id === store.currentConversationId }">
            <button type="button" class="session-select" :aria-label="`打开会话：${conversation.title}`" :aria-current="conversation.id === store.currentConversationId ? 'page' : undefined" @click="openConversation(conversation.id)">
              <span class="session-title">{{ conversation.title }}</span>
              <span class="session-meta"><span class="session-status" :data-state="statusLabel(conversation.latest_job_status)">{{ statusLabel(conversation.latest_job_status) }}</span><span>{{ formatDate(conversation.updated_at) }}</span></span>
            </button>
            <button type="button" class="session-delete" :aria-label="`删除会话：${conversation.title}`" @click="remove(conversation.id, $event)">删除</button>
          </li>
        </ul>
      </div>
    </template>
    <div class="sidebar-account">
      <button type="button" class="account" data-testid="account-button" :aria-label="auth.isAuthenticated ? `账号信息：${auth.displayName}` : '登录 / 注册'" @click="auth.openUserModal()">
        <span class="account-avatar" aria-hidden="true">{{ initial }}</span>
        <span class="account-text"><span class="account-name" data-testid="account-name">{{ auth.displayName || "登录 / 注册" }}</span><span class="account-role">{{ !auth.isAuthenticated ? "登录后开启你的装机对话" : auth.isAdmin ? "管理员" : "普通用户" }}</span></span>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.sidebar { display: flex; flex-direction: column; width: 300px; flex: 0 0 300px; min-height: 0; height: 100%; border: var(--border-width-frame) solid var(--color-border); border-radius: 18px; overflow: hidden; background: var(--color-panel); background-clip: padding-box; }.sidebar-logo { flex-shrink: 0; padding: var(--space-lg); }.module-nav { display: grid; flex-shrink: 0; gap: 6px; padding: 12px; }.module-nav button { display: grid; gap: 5px; width: 100%; min-height: 62px; padding: 11px 14px; border: var(--border-width-frame) solid transparent; border-radius: 8px; background: transparent; background-clip: padding-box; color: var(--color-terminal-fg-dim); font: inherit; text-align: left; cursor: pointer; }.module-nav button:hover, .module-nav button[aria-current] { border-color: var(--color-border); background: var(--color-terminal-bg-elevated); color: var(--color-terminal-fg); }.module-nav button[aria-current] span { font-weight: 600; }.module-nav small { color: var(--color-terminal-fg-dimmer); font-size: 11px; }.module-nav button:focus-visible, .session-item button:focus-visible { outline: 2px solid var(--color-accent-cyan); outline-offset: 2px; }.section-header { display: flex; align-items: center; justify-content: space-between; padding: .8rem 1.15rem; }.section-header h2 { margin: 0; color: var(--color-terminal-fg); font: 600 var(--font-size-sm) var(--font-mono); }.session-scrollarea { flex: 1; min-height: 0; overflow-y: auto; scrollbar-color: #30363d transparent; }.session-empty { padding: var(--space-xl); color: var(--color-terminal-fg-dimmer); font: var(--font-size-xs) var(--font-mono); }.session-list { margin: 0; padding: .5rem 0; list-style: none; }.session-item { position: relative; display: flex; border-bottom: 1px solid transparent; }.session-item:hover, .session-item--active { background: var(--color-terminal-bg-elevated); }.session-item--active { border-bottom-color: var(--color-border); }.session-select { min-width: 0; flex: 1; padding: .85rem 3rem .85rem 1.25rem; border: 0; background: transparent; color: inherit; text-align: left; cursor: pointer; }.session-title, .session-summary, .session-meta { display: block; }.session-title, .session-summary { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.session-title { color: var(--color-terminal-fg); font-size: var(--font-size-sm); }.session-summary { margin-top: .25rem; color: var(--color-terminal-fg-dimmer); font-size: var(--font-size-xs); }.session-meta { display: flex; gap: .65rem; margin-top: .45rem; color: var(--color-terminal-fg-dimmer); font: 10px var(--font-mono); }.session-status[data-state="running"] { color: var(--color-status-warning, #d29922); }.session-status[data-state="failed"] { color: var(--color-status-error, #f85149); }.session-status[data-state="done"] { color: var(--color-status-success, #D97757); }.session-delete { position: absolute; top: .4rem; right: .25rem; min-width: 44px; min-height: 44px; border: 0; background: transparent; color: var(--color-terminal-fg-dimmer); font: 10px var(--font-mono); cursor: pointer; opacity: 0; }.session-item:hover .session-delete, .session-delete:focus-visible { opacity: 1; }.session-delete:hover { color: var(--color-status-error); }.sidebar-account { margin-top: auto; flex-shrink: 0; padding: .6rem .8rem; border-top: 1px solid var(--color-border); }.account { display: flex; width: 100%; min-height: 44px; align-items: center; gap: .55rem; padding: .35rem .5rem; border: var(--border-width-frame) solid transparent; border-radius: var(--radius-xs); background: transparent; background-clip: padding-box; color: inherit; text-align: left; cursor: pointer; }.account:hover { border-color: var(--color-border); background: var(--color-terminal-bg-elevated); }.account:focus-visible { outline: 2px solid var(--color-accent-cyan); outline-offset: 2px; }.account-avatar { display: grid; width: 1.6rem; height: 1.6rem; flex: 0 0 1.6rem; place-items: center; border: var(--border-width-frame) solid var(--color-status-success-dim); border-radius: 50%; color: var(--color-status-success); font: 600 var(--font-size-xs) var(--font-mono); background-clip: padding-box; }.account-text { display: grid; min-width: 0; }.account-name { overflow: hidden; color: var(--color-terminal-fg); font: var(--font-size-sm) var(--font-mono); text-overflow: ellipsis; white-space: nowrap; }.account-role { color: var(--color-terminal-fg-dimmer); font: 10px var(--font-mono); }
.admin-art { margin: 8px 0 0; color: #fff; -webkit-text-fill-color: #fff; font: .55rem/1.2 var(--font-mono); letter-spacing: -.05em; white-space: pre; word-break: keep-all; user-select: none; }
@media (max-width: 1100px) and (min-width: 761px) { .sidebar { width: 260px; flex-basis: 260px; } } @media (max-width: 760px) { .sidebar { flex: 0 0 auto; width: 100%; height: auto; max-height: 18rem; }.sidebar-logo { display: none; }.module-nav { display: flex; gap: 4px; padding: 4px 8px; overflow-x: auto; }.module-nav button { min-width: 128px; min-height: 44px; padding: 8px 10px; text-align: center; font-size: 13px; }.module-nav small { display: none; }.section-header { min-height: 42px; padding: 0 .8rem; }.section-header h2 { font-family: var(--font-sans); }.session-scrollarea { flex: 0 0 64px; overflow-x: auto; overflow-y: hidden; }.session-list { display: flex; width: max-content; padding: 0; }.session-item { width: 150px; flex: 0 0 150px; border-right: 1px solid var(--color-divider); border-bottom: 0; }.session-select { min-height: 64px; padding: .5rem 2.65rem .5rem .8rem; }.session-title { font-size: .75rem; }.session-summary { display: none; }.session-meta { margin-top: .25rem; }.session-delete { top: 10px; opacity: 1; }.session-item--active { border-bottom: 1px solid var(--color-status-success-dim); }.sidebar-account { flex-shrink: 0; padding: .25rem .6rem; }.account { min-height: 40px; }.account-role { display: none; } } @media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation-duration: .01ms !important; transition-duration: .01ms !important; } }
</style>
