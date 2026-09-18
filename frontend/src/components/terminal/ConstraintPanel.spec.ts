import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import type { Constraint } from "@/api/agentFusion";
import ConstraintPanel from "./ConstraintPanel.vue";

function fieldConstraint(overrides: Partial<Constraint> = {}): Constraint {
  return {
    constraint_id: "c1",
    kind: "hard",
    confirmed: true,
    target: "field",
    field_key: "gpu.vram_gib",
    operator: "gte",
    value: 12,
    unit: "gib",
    qualifier_key: "default",
    ...overrides,
  } as Constraint;
}

async function mountOpen(modelValue: Constraint[] = []) {
  const wrapper = mount(ConstraintPanel, { props: { modelValue } });
  await wrapper.find(".constraint-panel__toggle").trigger("click");
  return wrapper;
}

function lastEmitted(wrapper: ReturnType<typeof mount>): Constraint[] {
  const events = wrapper.emitted("update:modelValue");
  expect(events, "expected an update:modelValue emission").toBeTruthy();
  return events!.at(-1)![0] as Constraint[];
}

describe("ConstraintPanel", () => {
  it("stays collapsed until asked, so the composer keeps its size", async () => {
    const wrapper = mount(ConstraintPanel, { props: { modelValue: [fieldConstraint()] } });
    expect(wrapper.find(".constraint-panel__body").exists()).toBe(false);

    const toggle = wrapper.find(".constraint-panel__toggle");
    expect(toggle.attributes("aria-expanded")).toBe("false");
    expect(toggle.text()).toContain("1");
    expect(toggle.text()).toContain("1 hard");

    await toggle.trigger("click");
    expect(wrapper.find(".constraint-panel__body").exists()).toBe(true);
    expect(toggle.attributes("aria-expanded")).toBe("true");
  });

  it("adds a field constraint through the form and emits the full list", async () => {
    const wrapper = await mountOpen();
    expect(wrapper.text()).toContain("未设置约束");

    await wrapper.find(".constraint-panel__add").trigger("click");
    await wrapper.find('input[placeholder="gpu.vram_gib"]').setValue("gpu.vram_gib");
    await wrapper.find('input[placeholder="12"]').setValue("16");
    await wrapper.find(".constraint-form__submit").trigger("click");

    const emitted = lastEmitted(wrapper);
    expect(emitted).toHaveLength(1);
    expect(emitted[0]).toMatchObject({
      constraint_id: "c1",
      kind: "hard",
      target: "field",
      field_key: "gpu.vram_gib",
      operator: "gte",
      value: 16,
    });
  });

  it("keeps the value when the added constraint is a preference", async () => {
    const wrapper = await mountOpen();
    await wrapper.find(".constraint-panel__add").trigger("click");
    await wrapper.find("select[aria-label='约束性质']").setValue("preference");
    await wrapper.find('input[placeholder="gpu.vram_gib"]').setValue("gpu.board_power_w");
    await wrapper.findAll("select[aria-label='操作符']")[0].setValue("lte");
    await wrapper.find('input[placeholder="12"]').setValue("250");
    const weight = wrapper.findAll("input").find((input) => input.attributes("placeholder") === "1");
    expect(weight, "the weight input only exists for preferences").toBeTruthy();
    await weight!.setValue("0.5");
    await wrapper.find(".constraint-form__submit").trigger("click");

    const emitted = lastEmitted(wrapper);
    expect(emitted[0]).toMatchObject({
      kind: "preference",
      target: "field",
      field_key: "gpu.board_power_w",
      value: 250,
      weight: 0.5,
    });
  });

  it("surfaces a validation error instead of emitting an invalid constraint", async () => {
    const wrapper = await mountOpen();
    await wrapper.find(".constraint-panel__add").trigger("click");
    // No field key and no value: the form must refuse rather than send a guess.
    await wrapper.find(".constraint-form__submit").trigger("click");

    expect(wrapper.find(".constraint-form__error").text()).toContain("字段名");
    expect(wrapper.emitted("update:modelValue")).toBeFalsy();
  });

  it("offers the price fields when the target changes, and reopens empty", async () => {
    const wrapper = await mountOpen();
    await wrapper.find(".constraint-panel__add").trigger("click");
    await wrapper.find('input[placeholder="gpu.vram_gib"]').setValue("gpu.vram_gib");

    await wrapper.find("select[aria-label='约束目标']").setValue("price");
    expect(wrapper.find('input[placeholder="5000"]').exists()).toBe(true);
    // The field target's inputs are gone, so no stale value can leak into a price payload.
    expect(wrapper.find('input[placeholder="gpu.vram_gib"]').exists()).toBe(false);

    await wrapper.find(".constraint-form__cancel").trigger("click");
    await wrapper.find(".constraint-panel__add").trigger("click");
    // A second add must start from a blank draft, not the abandoned one.
    const fieldKey = wrapper.find('input[placeholder="gpu.vram_gib"]').element as HTMLInputElement;
    expect(fieldKey.value).toBe("");
  });

  it("edits an existing constraint in place without changing the list length", async () => {
    const existing = fieldConstraint();
    const wrapper = await mountOpen([existing]);

    const buttons = wrapper.findAll(".constraint-item__actions button");
    expect(buttons[0].text()).toBe("编辑");
    await buttons[0].trigger("click");

    const fieldKey = wrapper.find('input[placeholder="gpu.vram_gib"]').element as HTMLInputElement;
    expect(fieldKey.value).toBe("gpu.vram_gib");

    await wrapper.find('input[placeholder="12"]').setValue("24");
    await wrapper.find(".constraint-form__submit").trigger("click");

    const emitted = lastEmitted(wrapper);
    expect(emitted).toHaveLength(1);
    expect(emitted[0]).toMatchObject({ constraint_id: "c1", value: 24 });
  });

  it("removes a single constraint and can clear them all", async () => {
    const wrapper = await mountOpen([fieldConstraint(), fieldConstraint({ constraint_id: "c2" })]);

    const removeButtons = wrapper.findAll(".constraint-item__actions button")
      .filter((button) => button.text() === "删除");
    await removeButtons[0].trigger("click");
    expect(lastEmitted(wrapper).map((item) => item.constraint_id)).toEqual(["c2"]);

    await wrapper.setProps({ modelValue: [fieldConstraint(), fieldConstraint({ constraint_id: "c2" })] });
    await wrapper.find(".constraint-panel__clear").trigger("click");
    expect(lastEmitted(wrapper)).toEqual([]);
  });

  it("gives every new constraint a fresh id", async () => {
    const wrapper = await mountOpen([fieldConstraint()]);
    await wrapper.find(".constraint-panel__add").trigger("click");
    await wrapper.find('input[placeholder="gpu.vram_gib"]').setValue("gpu.vram_gib");
    await wrapper.find('input[placeholder="12"]').setValue("16");
    await wrapper.find(".constraint-form__submit").trigger("click");
    const emitted = lastEmitted(wrapper);
    expect(emitted.map((item) => item.constraint_id)).toEqual(["c1", "c2"]);
  });
});
