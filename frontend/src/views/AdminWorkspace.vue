<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppSidebar from "@/components/AppSidebar.vue";
import UsagePanel from "@/components/admin/UsagePanel.vue";
import UsersPanel from "@/components/admin/UsersPanel.vue";
import CatalogPanel from "@/components/admin/CatalogPanel.vue";
import "@/styles/admin-workspace.css";
const route = useRoute();
const router = useRouter();
const tabs = [{ id: "usage", label: "数据看板", hint: "Token 用量与请求耗时" },
  { id: "users", label: "用户管理", hint: "账号与访问" },
  { id: "catalog", label: "数据管理", hint: "硬件与模型目录" }];
const current = computed(() => tabs.some(tab => tab.id === route.query.section) ? String(route.query.section) : "usage");
</script>

<template>
  <div class="admin-workspace">
    <AppSidebar admin>
      <template #admin-navigation>
        <nav class="admin-nav" aria-label="后台导航">
          <button v-for="tab in tabs" :key="tab.id" type="button" :aria-current="current === tab.id ? 'page' : undefined"
            @click="router.push({ path: '/admin', query: { section: tab.id } })">
            <span>{{ tab.label }}</span><small>{{ tab.hint }}</small>
          </button>
        </nav>
      </template>
    </AppSidebar>
    <main class="admin-content" aria-label="RigBuilder 后台工作区">
      <UsagePanel v-if="current === 'usage'" />
      <UsersPanel v-else-if="current === 'users'" />
      <CatalogPanel v-else />
    </main>
  </div>
</template>

<style scoped>
.admin-workspace { display: flex; gap: 14px; height: 100dvh; overflow: hidden; padding: 14px; background: var(--color-terminal-bg); color: var(--color-terminal-fg); }
.admin-content { flex: 1; min-width: 0; min-height: 0; overflow-y: auto; border: var(--border-width-frame) solid var(--color-border); border-radius: 18px; background: var(--color-panel); background-clip: padding-box; }
.admin-nav { display: grid; gap: 6px; padding: 12px; }
.admin-nav button { display: grid; gap: 5px; width: 100%; padding: 14px 16px; text-align: left; border: var(--border-width-frame) solid transparent; border-radius: 8px; background: transparent; color: var(--color-terminal-fg-dim); cursor: pointer; font: inherit; background-clip: padding-box; }
.admin-nav button[aria-current], .admin-nav button:hover { background: var(--color-terminal-bg-elevated); border-color: var(--color-border); color: var(--color-terminal-fg); }
.admin-nav button[aria-current] span { font-weight: 600; }
.admin-nav small { color: var(--color-terminal-fg-dimmer); font-size: 12px; }
.admin-nav button:focus-visible { outline: 2px solid var(--color-accent-cyan); outline-offset: 2px; }
@media (max-width: 760px) { .admin-workspace { flex-direction: column; padding: 8px; gap: 8px; } .admin-content { border-radius: 14px; } }
@media (max-width: 760px) { .admin-nav { display: flex; padding: 4px 8px; gap: 4px; } .admin-nav button { padding: 10px; min-height: 44px; text-align: center; font-size: 13px; } .admin-nav small { display: none; } }
</style>
