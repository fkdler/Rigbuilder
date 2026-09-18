/**
 * Constraint draft ⇄ backend `Constraint` payload (Plan V4, §12-1 of Plan V3.6).
 *
 * `POST /api/query/jobs` already accepts four constraint targets (`field` /
 * `price` / `compatibility` / `runtime`) and two kinds (`hard` / `preference`),
 * and `fusion-v1` already evaluates both: a non-`satisfied` hard constraint
 * eliminates a candidate (`app/fusion/engine.py`), while preferences feed
 * `preference_utility`.  Until now the terminal sent a constant empty list, so
 * the whole path was unreachable from the interface.
 *
 * Everything in this module is a pure function, so the mapping rules can be
 * unit-tested without mounting a component.  The panel deliberately owns only
 * list state and never builds payloads itself.
 */

import type { Constraint, ConstraintKind, FieldOperator } from "@/api/agentFusion";

export type ConstraintTarget = Constraint["target"];
export type PriceOperator = "eq" | "ne" | "gt" | "gte" | "lt" | "lte";
export type ConstraintDirection = "outgoing" | "incoming";

export const CONSTRAINT_TARGETS: readonly ConstraintTarget[] = [
  "field",
  "price",
  "compatibility",
  "runtime",
];

export const TARGET_LABELS: Record<ConstraintTarget, string> = {
  field: "规格字段",
  price: "价格",
  compatibility: "兼容性",
  runtime: "运行时支持",
};

export const TARGET_HINTS: Record<ConstraintTarget, string> = {
  field: "按 Truth DB 中的字段值过滤，例如 gpu.vram_gib ≥ 12",
  price: "按最新价格快照过滤，币种与地区需与 Release 一致",
  compatibility: "要求候选与另一实体存在指定兼容关系",
  runtime: "要求候选支持某个运行时（例如 CUDA 版本）",
};

export const FIELD_OPERATORS: readonly FieldOperator[] = [
  "eq",
  "ne",
  "gt",
  "gte",
  "lt",
  "lte",
  "in",
  "contains",
];

export const PRICE_OPERATORS: readonly PriceOperator[] = ["lte", "lt", "eq", "ne", "gt", "gte"];

/** Operators whose right-hand side is a single number. */
const NUMERIC_OPERATORS: readonly FieldOperator[] = ["gt", "gte", "lt", "lte"];
/** Operators whose right-hand side is a set of values. */
const LIST_OPERATORS: readonly FieldOperator[] = ["in", "contains"];

export const OPERATOR_SYMBOLS: Record<FieldOperator, string> = {
  eq: "=",
  ne: "≠",
  gt: ">",
  gte: "≥",
  lt: "<",
  lte: "≤",
  in: "∈",
  contains: "⊇",
};

export const OPERATOR_LABELS: Record<FieldOperator, string> = {
  eq: "= 等于",
  ne: "≠ 不等于",
  gt: "> 大于",
  gte: "≥ 不小于",
  lt: "< 小于",
  lte: "≤ 不大于",
  in: "∈ 取值之一",
  contains: "⊇ 包含全部",
};

/**
 * Field keys that exist in the accepted Release, mirroring
 * `data/v3/releases/rigbuilder-v3-2-2026-09-07/field-definitions/`.
 * Served as an input suggestion list only — the backend validates the real key
 * and reports `unknown` for anything the Release does not define.
 */
export const FIELD_KEY_SUGGESTIONS: readonly string[] = [
  "gpu.vram_gib",
  "gpu.board_power_w",
  "gpu.architecture",
  "gpu.memory_type",
  "gpu.memory_bandwidth_gb_s",
  "gpu.memory_bus_width_bit",
  "gpu.pcie_generation",
  "gpu.pcie_lanes",
  "gpu.ecc_support",
  "cpu.cores_total",
  "cpu.threads",
  "cpu.socket",
  "cpu.architecture",
  "cpu.base_clock_mhz",
  "cpu.boost_clock_mhz",
  "cpu.base_power_w",
  "model.total_parameters",
  "model.context_length_tokens",
  "model.license_name",
  "entity.canonical_name",
];

export const PRICE_CONDITIONS: readonly string[] = ["new", "used"];
export const PRICE_TYPES: readonly string[] = ["current_new", "current_used"];
export const QUALIFIER_SUGGESTIONS: readonly string[] = ["default"];

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Editable form state. Every value is a string so the inputs stay uncontrolled-free. */
export interface ConstraintDraft {
  kind: ConstraintKind;
  target: ConstraintTarget;
  weight: string;

  field_key: string;
  operator: FieldOperator;
  value: string;
  unit: string;
  qualifier_key: string;

  currency: string;
  region: string;
  condition: string;
  price_type: string;

  other_entity_id: string;
  relation_key: string;
  direction: ConstraintDirection;

  required_status: string;
  runtime_key: string;
}

export function emptyDraft(
  kind: ConstraintKind = "hard",
  target: ConstraintTarget = "field",
): ConstraintDraft {
  return {
    kind,
    target,
    weight: "1",
    field_key: "",
    operator: target === "price" ? "lte" : "gte",
    value: "",
    unit: "",
    qualifier_key: "default",
    currency: "CNY",
    region: "CN",
    condition: "new",
    price_type: "current_new",
    other_entity_id: "",
    relation_key: "",
    direction: "outgoing",
    required_status: target === "runtime" ? "supported" : "compatible",
    runtime_key: "",
  };
}

/**
 * Reset the target-specific fields when the target changes.
 *
 * Without this, switching from `compatibility` (required_status `compatible`)
 * to `runtime` would leave `compatible` behind, and the runtime gate compares
 * against `supported` — producing a constraint that can never be satisfied.
 */
export function switchTarget(draft: ConstraintDraft, target: ConstraintTarget): ConstraintDraft {
  const next = emptyDraft(draft.kind, target);
  next.weight = draft.weight;
  return next;
}

/** Stable, human-readable ids; the backend requires uniqueness within one request. */
export function nextConstraintId(existing: readonly Constraint[]): string {
  const used = new Set(existing.map((item) => item.constraint_id));
  for (let index = 1; index < 10_000; index += 1) {
    const candidate = `c${index}`;
    if (!used.has(candidate)) return candidate;
  }
  return `c${Date.now()}`;
}

export function kindLabel(kind: ConstraintKind): string {
  return kind === "hard" ? "硬约束" : "偏好";
}

export function operatorOptions(target: ConstraintTarget): readonly FieldOperator[] {
  return target === "price" ? PRICE_OPERATORS : FIELD_OPERATORS;
}

export function valuePlaceholder(target: ConstraintTarget, operator: FieldOperator): string {
  if (target === "price") return "5000";
  if (LIST_OPERATORS.includes(operator)) return "12, 16, 24";
  if (NUMERIC_OPERATORS.includes(operator)) return "12";
  return "gddr6x";
}

export type DraftConversion =
  | { ok: true; constraint: Constraint }
  | { ok: false; error: string };

/** Turn an editable draft into the request payload, or explain what is missing. */
export function draftToConstraint(draft: ConstraintDraft, constraintId: string): DraftConversion {
  const id = constraintId.trim();
  if (!id) return { ok: false, error: "约束 id 不能为空。" };
  if (id.length > 120) return { ok: false, error: "约束 id 不能超过 120 个字符。" };

  let weight = 1;
  if (draft.kind === "preference") {
    const parsed = Number(draft.weight.trim());
    if (!draft.weight.trim() || !Number.isFinite(parsed) || parsed <= 0 || parsed > 1) {
      return { ok: false, error: "偏好权重必须是 (0, 1] 之间的数值。" };
    }
    weight = parsed;
  }

  switch (draft.target) {
    case "field":
      return fieldConversion(draft, id, weight);
    case "price":
      return priceConversion(draft, id, weight);
    case "compatibility":
      return compatibilityConversion(draft, id, weight);
    default:
      return runtimeConversion(draft, id, weight);
  }
}

function fieldConversion(
  draft: ConstraintDraft,
  id: string,
  weight: number,
): DraftConversion {
  const fieldKey = draft.field_key.trim();
  if (!fieldKey) return { ok: false, error: "请填写字段名，例如 gpu.vram_gib。" };

  const operator = draft.operator;
  const raw = draft.value.trim();
  let value: unknown;

  if (NUMERIC_OPERATORS.includes(operator)) {
    const parsed = Number(raw);
    if (!raw || !Number.isFinite(parsed)) {
      return { ok: false, error: `操作符「${OPERATOR_SYMBOLS[operator]}」需要一个数值。` };
    }
    value = parsed;
  } else if (LIST_OPERATORS.includes(operator)) {
    const items = parseList(raw);
    if (!items.length) return { ok: false, error: "请用逗号分隔至少一个取值。" };
    value = items;
  } else {
    if (!raw) return { ok: false, error: "请填写比较值。" };
    value = coerceScalar(raw);
  }

  const unit = draft.unit.trim();
  const qualifier = draft.qualifier_key.trim();
  return {
    ok: true,
    constraint: {
      constraint_id: id,
      kind: draft.kind,
      confirmed: true,
      weight,
      target: "field",
      field_key: fieldKey,
      operator,
      value,
      ...(unit ? { unit } : {}),
      ...(qualifier ? { qualifier_key: qualifier } : {}),
    },
  };
}

function priceConversion(
  draft: ConstraintDraft,
  id: string,
  weight: number,
): DraftConversion {
  const raw = draft.value.trim();
  const parsed = Number(raw);
  if (!raw || !Number.isFinite(parsed) || parsed < 0) {
    return { ok: false, error: "价格必须是不小于 0 的数值。" };
  }
  if (!isPriceOperator(draft.operator)) {
    return { ok: false, error: "价格约束只支持 = ≠ > ≥ < ≤ 六种比较。" };
  }
  const currency = draft.currency.trim().toUpperCase() || "CNY";
  if (!/^[A-Za-z]{3}$/.test(currency)) {
    return { ok: false, error: "币种必须是 3 位 ISO 代码，例如 CNY。" };
  }
  const region = draft.region.trim();
  const condition = draft.condition.trim();
  const priceType = draft.price_type.trim();
  return {
    ok: true,
    constraint: {
      constraint_id: id,
      kind: draft.kind,
      confirmed: true,
      weight,
      target: "price",
      operator: draft.operator,
      value: parsed,
      currency,
      ...(region ? { region } : {}),
      ...(condition ? { condition } : {}),
      ...(priceType ? { price_type: priceType } : {}),
    },
  };
}

function compatibilityConversion(
  draft: ConstraintDraft,
  id: string,
  weight: number,
): DraftConversion {
  const other = draft.other_entity_id.trim();
  if (!other) return { ok: false, error: "请填写对方实体的 UUID。" };
  if (!UUID_PATTERN.test(other)) {
    return { ok: false, error: "对方实体 ID 必须是标准 UUID（36 位十六进制）。" };
  }
  const relation = draft.relation_key.trim();
  if (!relation) return { ok: false, error: "请填写关系键，例如 runs_on。" };
  return {
    ok: true,
    constraint: {
      constraint_id: id,
      kind: draft.kind,
      confirmed: true,
      weight,
      target: "compatibility",
      other_entity_id: other,
      relation_key: relation,
      direction: draft.direction,
      required_status: draft.required_status.trim() || "compatible",
    },
  };
}

function runtimeConversion(
  draft: ConstraintDraft,
  id: string,
  weight: number,
): DraftConversion {
  const key = draft.runtime_key.trim();
  if (!key) return { ok: false, error: "请填写运行时键，例如 cuda.runtime。" };
  return {
    ok: true,
    constraint: {
      constraint_id: id,
      kind: draft.kind,
      confirmed: true,
      weight,
      target: "runtime",
      runtime_key: key,
      required_status: draft.required_status.trim() || "supported",
    },
  };
}

/** Rebuild an editable draft from a saved constraint, for the edit path. */
export function draftFromConstraint(constraint: Constraint): ConstraintDraft {
  const draft = emptyDraft(constraint.kind, constraint.target);
  draft.weight = String(constraint.weight ?? 1);
  switch (constraint.target) {
    case "field":
      draft.field_key = constraint.field_key;
      draft.operator = constraint.operator;
      draft.value = formatValueInput(constraint.value);
      draft.unit = constraint.unit ?? "";
      draft.qualifier_key = constraint.qualifier_key ?? "default";
      break;
    case "price":
      draft.operator = constraint.operator;
      draft.value = String(constraint.value);
      draft.currency = constraint.currency ?? "CNY";
      draft.region = constraint.region ?? "CN";
      draft.condition = constraint.condition ?? "new";
      draft.price_type = constraint.price_type ?? "current_new";
      break;
    case "compatibility":
      draft.other_entity_id = constraint.other_entity_id;
      draft.relation_key = constraint.relation_key;
      draft.direction = constraint.direction ?? "outgoing";
      draft.required_status = constraint.required_status ?? "compatible";
      break;
    default:
      draft.runtime_key = constraint.runtime_key;
      draft.required_status = constraint.required_status ?? "supported";
      break;
  }
  return draft;
}

/** One-line summary shown in the constraint list. */
export function formatConstraint(constraint: Constraint): string {
  switch (constraint.target) {
    case "field": {
      const unit = constraint.unit ? ` ${constraint.unit}` : "";
      const qualifier = constraint.qualifier_key && constraint.qualifier_key !== "default"
        ? ` @${constraint.qualifier_key}`
        : "";
      return `${constraint.field_key} ${OPERATOR_SYMBOLS[constraint.operator]} ${formatValue(constraint.value)}${unit}${qualifier}`;
    }
    case "price": {
      const currency = constraint.currency ?? "CNY";
      const qualifiers = [constraint.region, constraint.condition, constraint.price_type]
        .filter((item): item is string => Boolean(item))
        .join(" / ");
      const suffix = qualifiers ? ` (${qualifiers})` : "";
      return `价格 ${OPERATOR_SYMBOLS[constraint.operator]} ${constraint.value} ${currency}${suffix}`;
    }
    case "compatibility": {
      const arrow = constraint.direction === "incoming" ? "←" : "→";
      return `${constraint.relation_key} ${arrow} ${shortId(constraint.other_entity_id)} 需为 ${constraint.required_status ?? "compatible"}`;
    }
    default:
      return `${constraint.runtime_key} 需为 ${constraint.required_status ?? "supported"}`;
  }
}

export function formatValue(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => String(item)).join(", ");
  if (value === null || value === undefined) return "";
  return String(value);
}

function formatValueInput(value: unknown): string {
  return formatValue(value);
}

function coerceScalar(raw: string): unknown {
  const parsed = Number(raw);
  return raw !== "" && Number.isFinite(parsed) ? parsed : raw;
}

function parseList(raw: string): unknown[] {
  return raw
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
    .map(coerceScalar);
}

function isPriceOperator(operator: FieldOperator): operator is PriceOperator {
  return operator !== "in" && operator !== "contains";
}

function shortId(value: string): string {
  return value.length > 8 ? `${value.slice(0, 8)}…` : value;
}

/**
 * Request-level uniqueness guard.
 *
 * `QueryJobRequest` rejects duplicate `constraint_id` values, so the panel
 * checks before submitting rather than letting the backend answer with a 422.
 */
export function duplicateIds(constraints: readonly Constraint[]): string[] {
  const seen = new Set<string>();
  const duplicated = new Set<string>();
  for (const constraint of constraints) {
    const id = constraint.constraint_id;
    if (seen.has(id)) duplicated.add(id);
    seen.add(id);
  }
  return [...duplicated];
}


