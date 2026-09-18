import { describe, expect, it } from "vitest";

import type { Constraint } from "@/api/agentFusion";
import {
  duplicateIds,
  draftFromConstraint,
  draftToConstraint,
  emptyDraft,
  formatConstraint,
  nextConstraintId,
  switchTarget,
} from "./constraints";

const UUID = "11111111-1111-4111-8111-111111111111";

/** Small helper so every payload assertion reads as "this must survive the mapping". */
function convert(draft: ReturnType<typeof emptyDraft>, id = "c1"): Constraint {
  const result = draftToConstraint(draft, id);
  if (!result.ok) throw new Error(`expected a valid constraint, got: ${result.error}`);
  return result.constraint;
}

function errorOf(draft: ReturnType<typeof emptyDraft>): string {
  const result = draftToConstraint(draft, "c1");
  if (result.ok) throw new Error("expected the draft to be rejected");
  return result.error;
}

describe("emptyDraft / switchTarget", () => {
  it("defaults to a hard field constraint with the backend's own defaults", () => {
    const draft = emptyDraft();
    expect(draft.kind).toBe("hard");
    expect(draft.target).toBe("field");
    expect(draft.weight).toBe("1");
    expect(draft.qualifier_key).toBe("default");
    expect(draft.currency).toBe("CNY");
    expect(draft.region).toBe("CN");
    expect(draft.condition).toBe("new");
    expect(draft.price_type).toBe("current_new");
  });

  it("resets target-specific fields so a stale status cannot survive a target switch", () => {
    // compatibility compares against `compatible`, runtime against `supported`.
    // Carrying the former into a runtime constraint produces a gate that can
    // never be satisfied, so the switch must clear it.
    const compatibility = emptyDraft("hard", "compatibility");
    compatibility.other_entity_id = UUID;
    compatibility.relation_key = "runs_on";
    compatibility.required_status = "compatible";

    const runtime = switchTarget(compatibility, "runtime");
    expect(runtime.target).toBe("runtime");
    expect(runtime.required_status).toBe("supported");
    expect(runtime.other_entity_id).toBe("");
    expect(runtime.relation_key).toBe("");

    const back = switchTarget(runtime, "compatibility");
    expect(back.required_status).toBe("compatible");
    expect(back.runtime_key).toBe("");
  });

  it("keeps the kind and weight across a target switch", () => {
    const draft = emptyDraft("preference", "field");
    draft.weight = "0.4";
    const price = switchTarget(draft, "price");
    expect(price.kind).toBe("preference");
    expect(price.weight).toBe("0.4");
  });
});

describe("nextConstraintId", () => {
  it("starts at c1 and skips ids already in use", () => {
    expect(nextConstraintId([])).toBe("c1");
    const used = [
      { constraint_id: "c1" },
      { constraint_id: "c3" },
    ] as Constraint[];
    expect(nextConstraintId(used)).toBe("c2");
  });
});

describe("draftToConstraint — field", () => {
  it("maps a numeric comparison to a number payload", () => {
    const draft = emptyDraft("hard", "field");
    draft.field_key = "gpu.vram_gib";
    draft.operator = "gte";
    draft.value = "12";
    draft.unit = "gib";

    expect(convert(draft)).toMatchObject({
      constraint_id: "c1",
      kind: "hard",
      confirmed: true,
      target: "field",
      field_key: "gpu.vram_gib",
      operator: "gte",
      value: 12,
      unit: "gib",
      qualifier_key: "default",
    });
  });

  it("keeps a textual comparison as a string", () => {
    const draft = emptyDraft("hard", "field");
    draft.field_key = "gpu.memory_type";
    draft.operator = "eq";
    draft.value = "gddr6x";
    const constraint = convert(draft);
    expect(constraint).toMatchObject({ target: "field", value: "gddr6x" });
  });

  it("splits a comma separated list for the set operators", () => {
    const draft = emptyDraft("hard", "field");
    draft.field_key = "gpu.vram_gib";
    draft.operator = "in";
    draft.value = "8, 12, 16";
    expect(convert(draft)).toMatchObject({ target: "field", operator: "in", value: [8, 12, 16] });
  });

  it("rejects a non-numeric value for a numeric operator", () => {
    const draft = emptyDraft("hard", "field");
    draft.field_key = "gpu.vram_gib";
    draft.operator = "gte";
    draft.value = "many";
    expect(errorOf(draft)).toContain("数值");
  });

  it("rejects a missing field key and an empty comparison value", () => {
    const missingField = emptyDraft("hard", "field");
    missingField.value = "12";
    expect(errorOf(missingField)).toContain("字段名");

    const missingValue = emptyDraft("hard", "field");
    missingValue.field_key = "gpu.vram_gib";
    missingValue.operator = "eq";
    expect(errorOf(missingValue)).toContain("比较值");
  });

  it("rejects an empty set", () => {
    const draft = emptyDraft("hard", "field");
    draft.field_key = "gpu.vram_gib";
    draft.operator = "in";
    draft.value = " , , ";
    expect(errorOf(draft)).toContain("逗号");
  });
});

describe("draftToConstraint — price", () => {
  it("maps a budget to a price constraint and upper-cases the currency", () => {
    const draft = emptyDraft("hard", "price");
    draft.operator = "lte";
    draft.value = "5000";
    draft.currency = "cny";
    expect(convert(draft)).toMatchObject({
      target: "price",
      operator: "lte",
      value: 5000,
      currency: "CNY",
      region: "CN",
      condition: "new",
      price_type: "current_new",
    });
  });

  it("rejects a negative amount, a bad currency and a set operator", () => {
    const negative = emptyDraft("hard", "price");
    negative.value = "-1";
    expect(errorOf(negative)).toContain("不小于 0");

    const badCurrency = emptyDraft("hard", "price");
    badCurrency.value = "100";
    badCurrency.currency = "RMBX";
    expect(errorOf(badCurrency)).toContain("3 位");

    const setOperator = emptyDraft("hard", "price");
    setOperator.value = "100";
    setOperator.operator = "in";
    expect(errorOf(setOperator)).toContain("六种比较");
  });
});

describe("draftToConstraint — compatibility", () => {
  it("accepts a UUID and defaults the required status", () => {
    const draft = emptyDraft("hard", "compatibility");
    draft.other_entity_id = UUID.toUpperCase();
    draft.relation_key = "runs_on";
    draft.required_status = "";
    expect(convert(draft)).toMatchObject({
      target: "compatibility",
      other_entity_id: UUID.toUpperCase(),
      relation_key: "runs_on",
      direction: "outgoing",
      required_status: "compatible",
    });
  });

  it("rejects a malformed entity id and a missing relation key", () => {
    const badId = emptyDraft("hard", "compatibility");
    badId.other_entity_id = "rtx-4060";
    badId.relation_key = "runs_on";
    expect(errorOf(badId)).toContain("UUID");

    const badRelation = emptyDraft("hard", "compatibility");
    badRelation.other_entity_id = UUID;
    expect(errorOf(badRelation)).toContain("关系键");
  });
});

describe("draftToConstraint — runtime", () => {
  it("defaults the required status to supported, not compatible", () => {
    const draft = emptyDraft("hard", "runtime");
    draft.runtime_key = "cuda.runtime";
    draft.required_status = "";
    expect(convert(draft)).toMatchObject({ target: "runtime", required_status: "supported" });
  });

  it("rejects a missing runtime key", () => {
    expect(errorOf(emptyDraft("hard", "runtime"))).toContain("运行时键");
  });
});

describe("draftToConstraint — kind and weight", () => {
  it("carries the preference weight", () => {
    const draft = emptyDraft("preference", "field");
    draft.field_key = "gpu.board_power_w";
    draft.operator = "lte";
    draft.value = "250";
    draft.weight = "0.35";
    expect(convert(draft)).toMatchObject({ kind: "preference", weight: 0.35 });
  });

  it("forces the weight to 1 for hard constraints", () => {
    const draft = emptyDraft("hard", "field");
    draft.field_key = "gpu.vram_gib";
    draft.operator = "gte";
    draft.value = "12";
    draft.weight = "0.2";
    expect(convert(draft)).toMatchObject({ kind: "hard", weight: 1 });
  });

  it("rejects a weight outside (0, 1]", () => {
    for (const weight of ["0", "-0.5", "1.5", "", "abc"]) {
      const draft = emptyDraft("preference", "field");
      draft.field_key = "gpu.vram_gib";
      draft.operator = "gte";
      draft.value = "12";
      draft.weight = weight;
      expect(errorOf(draft), `weight ${JSON.stringify(weight)} must be rejected`).toContain("权重");
    }
  });
});

describe("draftFromConstraint round trip", () => {
  it("restores every target so editing never silently drops a field", () => {
    const drafts = [
      (() => {
        const draft = emptyDraft("hard", "field");
        draft.field_key = "gpu.vram_gib";
        draft.operator = "in";
        draft.value = "12, 16";
        draft.unit = "gib";
        return draft;
      })(),
      (() => {
        const draft = emptyDraft("preference", "price");
        draft.operator = "lte";
        draft.value = "5000";
        draft.weight = "0.8";
        return draft;
      })(),
      (() => {
        const draft = emptyDraft("hard", "compatibility");
        draft.other_entity_id = UUID;
        draft.relation_key = "runs_on";
        draft.direction = "incoming";
        return draft;
      })(),
      (() => {
        const draft = emptyDraft("hard", "runtime");
        draft.runtime_key = "cuda.runtime";
        return draft;
      })(),
    ];

    for (const draft of drafts) {
      const constraint = convert(draft);
      const restored = draftFromConstraint(constraint);
      // Rebuilding from the restored draft must reproduce the same payload.
      expect(convert(restored, constraint.constraint_id)).toEqual(constraint);
    }
  });
});

describe("formatConstraint", () => {
  it("renders one readable line per target", () => {
    const field = emptyDraft("hard", "field");
    field.field_key = "gpu.vram_gib";
    field.operator = "gte";
    field.value = "12";
    field.unit = "gib";
    expect(formatConstraint(convert(field))).toBe("gpu.vram_gib ≥ 12 gib");

    const price = emptyDraft("hard", "price");
    price.value = "5000";
    price.operator = "lte";
    expect(formatConstraint(convert(price))).toBe("价格 ≤ 5000 CNY (CN / new / current_new)");

    const compatibility = emptyDraft("hard", "compatibility");
    compatibility.other_entity_id = UUID;
    compatibility.relation_key = "runs_on";
    expect(formatConstraint(convert(compatibility))).toContain("runs_on → 11111111…");

    const runtime = emptyDraft("hard", "runtime");
    runtime.runtime_key = "cuda.runtime";
    expect(formatConstraint(convert(runtime))).toBe("cuda.runtime 需为 supported");
  });
});

describe("duplicateIds", () => {
  it("reports the ids the backend would reject as a duplicate", () => {
    // QueryJobRequest.unique_constraint_ids answers 422 on duplicates.
    const field = emptyDraft("hard", "field");
    field.field_key = "gpu.vram_gib";
    field.operator = "gte";
    field.value = "12";
    const first = convert(field, "dup");
    const second = convert(field, "dup");
    const third = convert(field, "other");

    expect(duplicateIds([first, second, third])).toEqual(["dup"]);
    expect(duplicateIds([first, third])).toEqual([]);
  });
});
