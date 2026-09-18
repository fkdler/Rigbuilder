<script setup lang="ts">
/**
 * Constraint list + add/edit/remove for the terminal composer.
 *
 * This is the missing interface entry for a capability the backend already has:
 * `QueryJobRequest.constraints` accepts four targets (field / price /
 * compatibility / runtime) and two kinds (hard / preference), and `fusion-v1`
 * already evaluates both.  The composer used to call `store.submit(text, id)`
 * with two arguments, so `constraints` was always an empty array and no user
 * could ever reach that code path.
 *
 * Follows the terminal's existing disclosure idiom (`metrics` / `evidence` /
 * `execution trace`): collapsed by default, so the composer keeps its size.
 */
import { computed, ref } from "vue";

import type { Constraint } from "@/api/agentFusion";
import ConstraintForm from "./ConstraintForm.vue";
import { formatConstraint, nextConstraintId } from "./constraints";

const props = defineProps<{
  modelValue: Constraint[];
  disabled?: boolean;
}>();

const emit = defineEmits<{
  "update:modelValue": [constraints: Constraint[]];
}>();

const open = ref(false);
const adding = ref(false);
const editingId = ref<string | null>(null);
/** Forces the form to remount, so a draft is never carried into the next edit. */
const formSeq = ref(0);

const formOpen = computed(() => adding.value || editingId.value !== null);

const formId = computed(() =>
  adding.value ? nextConstraintId(props.modelValue) : editingId.value ?? "",
);

const formInitial = computed<Constraint | undefined>(() => {
  if (adding.value || editingId.value === null) return undefined;
  return props.modelValue.find((item) => item.constraint_id === editingId.value);
});

const hardCount = computed(() => props.modelValue.filter((item) => item.kind === "hard").length);
const preferenceCount = computed(() =>
  props.modelValue.filter((item) => item.kind === "preference").length,
);

function toggle(): void {
  open.value = !open.value;
}

function startAdd(): void {
  adding.value = true;
  editingId.value = null;
  formSeq.value += 1;
}

function startEdit(constraint: Constraint): void {
  adding.value = false;
  editingId.value = constraint.constraint_id;
  formSeq.value += 1;
}

function closeForm(): void {
  adding.value = false;
  editingId.value = null;
}

function onSave(constraint: Constraint): void {
  if (adding.value) {
    emit("update:modelValue", [...props.modelValue, constraint]);
  } else {
    emit(
      "update:modelValue",
      props.modelValue.map((item) =>
        item.constraint_id === constraint.constraint_id ? constraint : item,
      ),
    );
  }
  closeForm();
}

function remove(constraintId: string): void {
  emit(
    "update:modelValue",
    props.modelValue.filter((item) => item.constraint_id !== constraintId),
  );
  if (editingId.value === constraintId) closeForm();
}

function clear(): void {
  emit("update:modelValue", []);
  closeForm();
}
</script>

<template>
  <section class="constraint-panel">
    <div class="constraint-panel__bar">
      <button type="button" class="constraint-panel__toggle" :aria-expanded="open" @click="toggle">
        <i aria-hidden="true"></i>
        <span>constraints</span>
        <b v-if="modelValue.length" class="constraint-panel__count">{{ modelValue.length }}</b>
        <span v-if="hardCount" class="constraint-panel__kind" data-kind="hard">{{ hardCount }} hard</span>
        <span v-if="preferenceCount" class="constraint-panel__kind" data-kind="preference">{{ preferenceCount }} pref</span>
        <span v-if="!modelValue.length" class="constraint-panel__none">none</span>
      </button>
    </div>

    <div v-if="open" class="constraint-panel__body">
      <p v-if="!modelValue.length && !formOpen" class="constraint-panel__empty">
        未设置约束。约束以结构化字段提交给融合引擎：硬约束不满足的候选会被淘汰，偏好参与加权排序。
      </p>

      <ul v-if="modelValue.length" class="constraint-list">
        <li
          v-for="item in modelValue"
          :key="item.constraint_id"
          class="constraint-item"
          :title="`${item.constraint_id} · ${formatConstraint(item)}`"
        >
          <span class="constraint-item__kind" :data-kind="item.kind">
            {{ item.kind === "hard" ? "硬" : "偏好" }}
          </span>
          <code class="constraint-item__text">{{ formatConstraint(item) }}</code>
          <span class="constraint-item__actions">
            <button type="button" :disabled="disabled" @click="startEdit(item)">编辑</button>
            <button type="button" :disabled="disabled" @click="remove(item.constraint_id)">删除</button>
          </span>
        </li>
      </ul>

      <ConstraintForm
        v-if="formOpen"
        :key="formSeq"
        :constraint-id="formId"
        :initial="formInitial"
        :submit-label="adding ? '添加约束' : '保存修改'"
        @save="onSave"
        @cancel="closeForm"
      />

      <div v-else class="constraint-panel__actions">
        <button type="button" class="constraint-panel__add" :disabled="disabled" @click="startAdd">
          + 添加约束
        </button>
        <button
          v-if="modelValue.length"
          type="button"
          class="constraint-panel__clear"
          :disabled="disabled"
          @click="clear"
        >
          全部清除
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.constraint-panel { border-top: 0; }

.constraint-panel__bar { display: flex; }
.constraint-panel__toggle {
  display: flex;
  min-height: 34px;
  align-items: center;
  gap: .5rem;
  padding: 0 .1rem;
  border: 0;
  background: transparent;
  color: var(--color-terminal-fg-dim);
  font: .7rem var(--font-mono);
  cursor: pointer;
}
.constraint-panel__toggle:hover { color: var(--color-terminal-fg); }
.constraint-panel__toggle:focus-visible { outline: 2px solid var(--color-accent-cyan); outline-offset: 2px; }
.constraint-panel__toggle i {
  width: 7px;
  height: 7px;
  border-right: 1px solid currentColor;
  border-bottom: 1px solid currentColor;
  transform: rotate(-45deg);
  transition: transform var(--transition-base);
}
.constraint-panel__toggle[aria-expanded="true"] i { transform: rotate(45deg) translate(-1px, -1px); }
.constraint-panel__count { color: var(--color-terminal-fg); font-weight: 600; }
.constraint-panel__kind { letter-spacing: .04em; }
.constraint-panel__kind[data-kind="hard"] { color: var(--color-status-error); }
.constraint-panel__kind[data-kind="preference"] { color: var(--color-status-warning); }
.constraint-panel__none { color: var(--color-terminal-fg-dimmer); }

.constraint-panel__body { display: grid; gap: .55rem; padding: .1rem 0 .35rem; }

.constraint-panel__empty {
  margin: 0;
  max-width: 74ch;
  color: var(--color-terminal-fg-dimmer);
  font: .7rem/1.6 var(--font-mono);
}

.constraint-list { display: grid; gap: .3rem; margin: 0; padding: 0; list-style: none; }
.constraint-item {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: .6rem;
  padding: .3rem .45rem;
  border: var(--border-width-frame) solid var(--color-border);
  border-radius: 2px;
  background: var(--color-terminal-bg-elevated); background-clip: padding-box; }
.constraint-item__kind {
  padding: .1rem .35rem;
  border: var(--border-width-frame) solid currentColor;
  border-radius: 2px;
  font: .62rem var(--font-mono); background-clip: padding-box; }
.constraint-item__kind[data-kind="hard"] { color: var(--color-status-error); }
.constraint-item__kind[data-kind="preference"] { color: var(--color-status-warning); }
.constraint-item__text { min-width: 0; overflow-wrap: anywhere; color: var(--color-terminal-fg-dim); font: .72rem var(--font-mono); }
.constraint-item__actions { display: flex; gap: .2rem; }
.constraint-item__actions button {
  min-height: 30px;
  padding: 0 .4rem;
  border: 0;
  background: transparent;
  color: var(--color-terminal-fg-dimmer);
  font: .66rem var(--font-mono);
  cursor: pointer;
}
.constraint-item__actions button:hover:not(:disabled) { color: var(--color-terminal-fg); }
.constraint-item__actions button:disabled { cursor: not-allowed; opacity: .5; }
.constraint-item__actions button:focus-visible { outline: 2px solid var(--color-accent-cyan); outline-offset: 2px; }

.constraint-panel__actions { display: flex; flex-wrap: wrap; gap: .3rem .9rem; align-items: center; }
.constraint-panel__add,
.constraint-panel__clear {
  min-height: 32px;
  padding: 0 .1rem;
  border: 0;
  background: transparent;
  font: .7rem var(--font-mono);
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: .22em;
}
.constraint-panel__add { color: var(--color-status-success); }
.constraint-panel__clear { color: var(--color-terminal-fg-dimmer); }
.constraint-panel__add:hover:not(:disabled) { color: var(--color-terminal-fg); }
.constraint-panel__clear:hover:not(:disabled) { color: var(--color-status-error); }
.constraint-panel__add:disabled,
.constraint-panel__clear:disabled { cursor: not-allowed; opacity: .5; }
.constraint-panel__add:focus-visible,
.constraint-panel__clear:focus-visible { outline: 2px solid var(--color-accent-cyan); outline-offset: 2px; }

@media (max-width: 620px) {
  .constraint-item { grid-template-columns: auto minmax(0, 1fr); }
  .constraint-item__actions { grid-column: 2; }
  .constraint-panel__toggle,
  .constraint-panel__add,
  .constraint-panel__clear { min-height: 44px; }
  .constraint-item__actions button { min-height: 44px; }
}
</style>
