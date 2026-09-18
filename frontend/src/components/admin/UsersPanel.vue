<script setup lang="ts">
import { nextTick, onMounted, onBeforeUnmount, ref } from "vue";
import { adminError, dateTime, deleteUser, getUsers, type AdminUser } from "@/api/adminWorkspace";

const query = ref("");
const appliedQuery = ref("");
const offset = ref(0);
const total = ref(0);
const rows = ref<AdminUser[]>([]);
const busy = ref(false);
const deleting = ref(false);
const error = ref("");
const deleteError = ref("");
const message = ref("");
const selected = ref<AdminUser | null>(null);
const confirmation = ref("");
const dialog = ref<HTMLDialogElement | null>(null);
const confirmInput = ref<HTMLInputElement | null>(null);
let generation = 0;
async function load() {
  const request = ++generation;
  busy.value = true; error.value = "";
  try {
    const data = await getUsers(appliedQuery.value, offset.value);
    if (request !== generation) return;
    rows.value = data.items; total.value = data.total;
  } catch (cause) { if (request === generation) { error.value = adminError(cause); rows.value = []; } }
  finally { if (request === generation) busy.value = false; }
}
function search() { appliedQuery.value = query.value; offset.value = 0; void load(); }
function page(delta: number) { offset.value += delta; void load(); }
async function confirmDelete(user: AdminUser) {
  selected.value = user; confirmation.value = ""; deleteError.value = "";
  await nextTick(); dialog.value?.showModal(); confirmInput.value?.focus();
}
function close() { if (deleting.value) return; dialog.value?.close(); selected.value = null; }
async function remove() {
  if (!selected.value || confirmation.value !== selected.value.username || deleting.value) return;
  deleting.value = true; deleteError.value = ""; message.value = "";
  try {
    await deleteUser(selected.value);
    message.value = `已删除账号「${selected.value.username}」。`;
    deleting.value = false; close();
    if (rows.value.length === 1 && offset.value > 0) offset.value -= 25;
    await load();
  } catch (cause) { deleteError.value = adminError(cause); }
  finally { deleting.value = false; }
}
onMounted(load);
onBeforeUnmount(() => { generation++; });
</script>

<template>
  <section class="admin-page" :aria-busy="busy">
    <header class="admin-heading"><div><h1>用户管理</h1><p>查看网站账号，管理用户访问。</p></div></header>
    <form class="admin-toolbar" @submit.prevent="search"><label>搜索用户<input v-model="query" type="search" maxlength="100" placeholder="输入用户名" /></label><button class="admin-button" :disabled="busy">搜索</button></form>
    <p v-if="error" class="admin-error" role="alert">{{ error }} <button class="admin-button" @click="load">重试</button></p>
    <p v-if="message" role="status" class="admin-success">{{ message }}</p>
    <p v-if="busy" role="status" class="admin-muted">正在加载账号…</p>
    <template v-else-if="!error">
      <div class="admin-table-wrap"><table><caption class="sr-only">网站账号</caption><thead><tr><th>用户名</th><th>角色 / 状态</th><th>注册时间</th><th>最近登录</th><th>操作</th></tr></thead><tbody>
        <tr v-for="user in rows" :key="user.id"><td>{{ user.username }}</td><td>{{ user.role === 'admin' ? '管理员' : '用户' }}<small>{{ user.status === 'active' ? '正常' : '已停用' }}</small></td><td>{{ dateTime(user.created_at) }}</td><td>{{ dateTime(user.last_login_at) }}</td><td><button v-if="user.role !== 'admin'" class="admin-button danger" :aria-label="`删除账号 ${user.username}`" @click="confirmDelete(user)">删除账户</button><span v-else class="admin-muted">受保护</span></td></tr>
      </tbody></table></div>
      <p v-if="!rows.length" class="admin-empty">{{ appliedQuery ? '没有匹配的用户，试试其他用户名。' : '暂无用户。' }}</p>
      <footer class="admin-pager"><span>共 {{ total }} 个账号 · 第 {{ Math.floor(offset / 25) + 1 }} 页</span><button class="admin-button" :disabled="offset === 0" @click="page(-25)">上一页</button><button class="admin-button" :disabled="offset + 25 >= total" @click="page(25)">下一页</button></footer>
    </template>
    <dialog ref="dialog" class="delete-dialog" aria-labelledby="delete-title" @cancel.prevent="close">
      <form v-if="selected" @submit.prevent="remove">
        <h2 id="delete-title">删除账号「{{ selected.username }}」？</h2>
        <p>账号及其会话、任务记录将被永久删除，所有登录会话立即失效。全站用量统计会保留。</p>
        <label>输入用户名以确认<input ref="confirmInput" v-model="confirmation" autocomplete="off" :disabled="deleting" /></label>
        <p v-if="deleteError" role="alert" class="admin-error">{{ deleteError }}</p>
        <div class="delete-actions"><button type="button" class="admin-button" :disabled="deleting" @click="close">取消</button><button class="admin-button danger" :disabled="deleting || confirmation !== selected.username">{{ deleting ? '删除中…' : '永久删除' }}</button></div>
      </form>
    </dialog>
  </section>
</template>

<style scoped>
.delete-dialog { width: min(460px, calc(100vw - 40px)); box-sizing: border-box; padding: 28px; border: var(--border-width-frame) solid var(--color-border-emphasis); border-radius: 14px; background: var(--color-panel); color: var(--color-terminal-fg); background-clip: padding-box; }
.delete-dialog::backdrop { background: rgb(0 0 0 / .7); }
.delete-dialog p { color: var(--color-terminal-fg-dim); margin: 16px 0 24px; }
.delete-actions { display: flex; justify-content: end; gap: 12px; margin-top: 24px; }
</style>
