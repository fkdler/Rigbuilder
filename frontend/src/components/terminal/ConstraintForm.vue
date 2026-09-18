<script setup lang="ts">
/**
 * Terminal-styled constraint editor form (Plan V4 / Plan V3.6 §12-1).
 *
 * The only thing this component owns is the draft.  Validation and payload
 * construction live in `constraints.ts` so they stay unit-testable; the panel
 * owns list state and the id, and rebuilds this form (`:key`) for each edit so
 * the draft never has to be synchronised with a prop.
 *
 * Replaces `components/ConstraintEditor.vue`, which could not work: it imported
 * the never-loaded `styles/tokens.css` (its CSS variables were undefined at
 * runtime), it only ever removed constraints, and it was only reachable from a
 * dead view.
 */
import { reactive, ref } from "vue";

import type { Constraint, ConstraintKind, FieldOperator } from "@/api/agentFusion";
import {
  CONSTRAINT_TARGETS,
  FIELD_KEY_SUGGESTIONS,
  FIELD_OPERATORS,
  OPERATOR_LABELS,
  PRICE_CONDITIONS,
  PRICE_TYPES,
  TARGET_HINTS,
  TARGET_LABELS,
  draftFromConstraint,
  draftToConstraint,
  emptyDraft,
  operatorOptions,
  switchTarget,
  valuePlaceholder,
  type ConstraintDirection,
  type ConstraintDraft,
  type ConstraintTarget,
} from "./constraints";

const props = defineProps<{
  constraintId: string;
  initial?: Constraint | null;
  submitLabel?: string;
}>();

const emit = defineEmits<{
  save: [constraint: Constraint];
  cancel: [];
}>();

const draft = reactive<ConstraintDraft>(
  props.initial ? draftFromConstraint(props.initial) : emptyDraft(),
);
const error = ref<string | null>(null);

function onKindChange(event: Event): void {
  draft.kind = (event.target as HTMLSelectElement).value as ConstraintKind;
}

function onTargetChange(event: Event): void {
  const target = (event.target as HTMLSelectElement).value as ConstraintTarget;
  // Reset the target-specific fields: a stale `required_status` from another
  // target would produce a constraint that can never be satisfied.
  Object.assign(draft, switchTarget(draft, target));
  error.value = null;
}

function onOperatorChange(event: Event): void {
  draft.operator = (event.target as HTMLSelectElement).value as FieldOperator;
}

function onDirectionChange(event: Event): void {
  draft.direction = (event.target as HTMLSelectElement).value as ConstraintDirection;
}

function onConditionChange(event: Event): void {
  draft.condition = (event.target as HTMLSelectElement).value;
}

function onPriceTypeChange(event: Event): void {
  draft.price_type = (event.target as HTMLSelectElement).value;
}

function save(): void {
  const result = draftToConstraint(draft, props.constraintId);
  if (!result.ok) {
    error.value = result.error;
    return;
  }
  error.value = null;
  emit("save", result.constraint);
}
</script>

<template>
  <!--
    A plain container, not a <form>: this editor is embedded in the composer's own
    form, and nested forms are illegal HTML.  Enter still saves, which is what a
    form inside a form would have given us.
  -->
  <div class="constraint-form" @keydown.enter.prevent="save">
    <div class="constraint-form__grid">
      <label class="cfield">
        <span class="cfield__label">约束性质</span>
        <select :value="draft.kind" aria-label="约束性质" @change="onKindChange">
          <option value="hard">硬约束 · 不满足即淘汰</option>
          <option value="preference">偏好 · 参与加权排序</option>
        </select>
      </label>

      <label class="cfield">
        <span class="cfield__label">约束目标</span>
        <select :value="draft.target" aria-label="约束目标" @change="onTargetChange">
          <option v-for="target in CONSTRAINT_TARGETS" :key="target" :value="target">
            {{ TARGET_LABELS[target] }}
          </option>
        </select>
      </label>

      <p class="cfield__hint cfield--wide">{{ TARGET_HINTS[draft.target] }}</p>

      <template v-if="draft.target === 'field'">
        <label class="cfield cfield--wide">
          <span class="cfield__label">字段 field_key</span>
          <input v-model="draft.field_key" list="rigbuilder-field-keys" placeholder="gpu.vram_gib" />
          <datalist id="rigbuilder-field-keys">
            <option v-for="key in FIELD_KEY_SUGGESTIONS" :key="key" :value="key" />
          </datalist>
        </label>
        <label class="cfield">
          <span class="cfield__label">操作符</span>
          <select :value="draft.operator" aria-label="操作符" @change="onOperatorChange">
            <option v-for="op in FIELD_OPERATORS" :key="op" :value="op">{{ OPERATOR_LABELS[op] }}</option>
          </select>
        </label>
        <label class="cfield">
          <span class="cfield__label">比较值</span>
          <input v-model="draft.value" :placeholder="valuePlaceholder(draft.target, draft.operator)" />
        </label>
        <label class="cfield">
          <span class="cfield__label">单位（可选）</span>
          <input v-model="draft.unit" placeholder="gib" />
        </label>
        <label class="cfield">
          <span class="cfield__label">限定键（可选）</span>
          <input v-model="draft.qualifier_key" placeholder="default" />
        </label>
      </template>

      <template v-else-if="draft.target === 'price'">
        <label class="cfield">
          <span class="cfield__label">操作符</span>
          <select :value="draft.operator" aria-label="价格操作符" @change="onOperatorChange">
            <option v-for="op in operatorOptions('price')" :key="op" :value="op">{{ OPERATOR_LABELS[op] }}</option>
          </select>
        </label>
        <label class="cfield">
          <span class="cfield__label">金额</span>
          <input v-model="draft.value" inputmode="decimal" placeholder="5000" />
        </label>
        <label class="cfield">
          <span class="cfield__label">币种</span>
          <input v-model="draft.currency" maxlength="3" placeholder="CNY" />
        </label>
        <label class="cfield">
          <span class="cfield__label">地区</span>
          <input v-model="draft.region" placeholder="CN" />
        </label>
        <label class="cfield">
          <span class="cfield__label">新旧</span>
          <select :value="draft.condition" aria-label="新旧" @change="onConditionChange">
            <option v-for="item in PRICE_CONDITIONS" :key="item" :value="item">{{ item }}</option>
          </select>
        </label>
        <label class="cfield">
          <span class="cfield__label">价格类型</span>
          <select :value="draft.price_type" aria-label="价格类型" @change="onPriceTypeChange">
            <option v-for="item in PRICE_TYPES" :key="item" :value="item">{{ item }}</option>
          </select>
        </label>
      </template>

      <template v-else-if="draft.target === 'compatibility'">
        <label class="cfield cfield--wide">
          <span class="cfield__label">对方实体 UUID</span>
          <input v-model="draft.other_entity_id" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" />
        </label>
        <label class="cfield">
          <span class="cfield__label">关系键 relation_key</span>
          <input v-model="draft.relation_key" placeholder="runs_on" />
        </label>
        <label class="cfield">
          <span class="cfield__label">方向</span>
          <select :value="draft.direction" aria-label="方向" @change="onDirectionChange">
            <option value="outgoing">outgoing · 本实体 → 对方</option>
            <option value="incoming">incoming · 对方 → 本实体</option>
          </select>
        </label>
        <label class="cfield">
          <span class="cfield__label">要求状态</span>
          <input v-model="draft.required_status" placeholder="compatible" />
        </label>
      </template>

      <template v-else>
        <label class="cfield">
          <span class="cfield__label">运行时键 runtime_key</span>
          <input v-model="draft.runtime_key" placeholder="cuda.runtime" />
        </label>
        <label class="cfield">
          <span class="cfield__label">要求状态</span>
          <input v-model="draft.required_status" placeholder="supported" />
        </label>
      </template>

      <label v-if="draft.kind === 'preference'" class="cfield">
        <span class="cfield__label">偏好权重 (0, 1]</span>
        <input v-model="draft.weight" inputmode="decimal" placeholder="1" />
      </label>
    </div>

    <p v-if="error" class="constraint-form__error" role="alert">{{ error }}</p>

    <div class="constraint-form__actions">
      <button type="button" class="constraint-form__submit" @click="save">{{ submitLabel ?? "保存约束" }}</button>
      <button type="button" class="constraint-form__cancel" @click="emit('cancel')">取消</button>
      <span class="constraint-form__id">id · {{ constraintId }}</span>
    </div>
  </div>
</template>

<style scoped>
.constraint-form {
  display: grid;
  gap: .55rem;
  padding: .7rem .8rem .75rem;
  border: var(--border-width-frame) solid var(--color-border);
  border-radius: 2px;
  background: var(--color-terminal-bg-elevated); background-clip: padding-box; }

.constraint-form__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(10.5rem, 1fr));
  gap: .5rem .75rem;
  align-items: start;
}

.cfield { display: grid; min-width: 0; gap: .22rem; }
.cfield--wide { grid-column: 1 / -1; }
.cfield__label { color: var(--color-terminal-fg-dimmer); font: .64rem var(--font-mono); letter-spacing: .05em; }
.cfield__hint { margin: 0; color: var(--color-terminal-fg-dimmer); font: .64rem/1.5 var(--font-mono); }

.cfield input,
.cfield select {
  min-width: 0;
  min-height: 32px;
  padding: .25rem .4rem;
  border: var(--border-width-frame) solid var(--color-border);
  border-radius: 2px;
  background: var(--color-terminal-bg);
  color: var(--color-terminal-fg);
  font: .75rem var(--font-mono); background-clip: padding-box; }
.cfield select { cursor: pointer; }
.cfield input::placeholder { color: var(--color-terminal-fg-dimmer); opacity: 1; }
.cfield input:hover,
.cfield select:hover { border-color: var(--color-border-emphasis); }
.cfield input:focus-visible,
.cfield select:focus-visible { outline: 2px solid var(--color-accent-cyan); outline-offset: 1px; }

.constraint-form__error {
  margin: 0;
  color: var(--color-status-error);
  font: .7rem/1.5 var(--font-mono);
}

.constraint-form__actions { display: flex; flex-wrap: wrap; align-items: center; gap: .35rem .8rem; }
.constraint-form__submit,
.constraint-form__cancel {
  min-height: 34px;
  padding: 0 .6rem;
  border: var(--border-width-frame) solid var(--color-border);
  border-radius: 2px;
  background: transparent;
  font: .72rem var(--font-mono);
  cursor: pointer; background-clip: padding-box; }
.constraint-form__submit { border-color: var(--color-status-success-dim); color: var(--color-status-success); }
.constraint-form__cancel { color: var(--color-terminal-fg-dim); }
.constraint-form__submit:hover { color: var(--color-terminal-fg); }
.constraint-form__cancel:hover { border-color: var(--color-border-emphasis); color: var(--color-terminal-fg); }
.constraint-form__submit:focus-visible,
.constraint-form__cancel:focus-visible { outline: 2px solid var(--color-accent-cyan); outline-offset: 2px; }
.constraint-form__id { margin-left: auto; color: var(--color-terminal-fg-dimmer); font: .62rem var(--font-mono); }

@media (max-width: 620px) {
  .cfield input,
  .cfield select { min-height: 44px; }
  .constraint-form__submit,
  .constraint-form__cancel { min-height: 44px; }
}
</style>
