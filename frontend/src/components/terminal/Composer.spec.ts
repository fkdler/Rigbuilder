import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useConversationStore } from "@/stores/conversations";
import { useAuthStore } from "@/stores/auth";
import Composer from "./Composer.vue";

/**
 * The composer used to call `store.submit(text, conversationId)` with two
 * arguments, so `customConstraints ?? []` was always `[]` and the backend's
 * whole constraint path — four targets, hard and preference kinds, all already
 * implemented and tested in `fusion-v1` — was unreachable from the interface.
 * These tests pin the third argument in place.
 */
describe("Composer constraint wiring", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    useAuthStore().user = { id: "user-1", username: "Ada", role: "user", created_at: "2026-09-16T00:00:00Z", last_login_at: null };
  });

  function spyOnSubmit() {
    const store = useConversationStore();
    return { store, submit: vi.spyOn(store, "submit").mockResolvedValue("conv-1") };
  }

  it("opens login without submitting or clearing an anonymous draft", async () => {
    useAuthStore().user = null;
    const { submit } = spyOnSubmit();
    const wrapper = mount(Composer);
    await wrapper.find("textarea").setValue("推荐配置");
    await wrapper.find("form.composer").trigger("submit");
    expect(useAuthStore().loginModalOpen).toBe(true);
    expect(submit).not.toHaveBeenCalled();
    expect(wrapper.find("textarea").element.value).toBe("推荐配置");
  });

  it("sends the configured constraint to the store on submit", async () => {
    const { submit } = spyOnSubmit();
    const wrapper = mount(Composer);

    await wrapper.find(".constraint-panel__toggle").trigger("click");
    await wrapper.find(".constraint-panel__add").trigger("click");
    await wrapper.find('input[placeholder="gpu.vram_gib"]').setValue("gpu.vram_gib");
    await wrapper.find('input[placeholder="12"]').setValue("16");
    await wrapper.find(".constraint-form__submit").trigger("click");

    await wrapper.find("textarea").setValue("推荐一张 16GB 以上显存的显卡");
    await wrapper.find("form.composer").trigger("submit");

    expect(submit).toHaveBeenCalledTimes(1);
    const call = submit.mock.calls[0];
    expect(call[0]).toBe("推荐一张 16GB 以上显存的显卡");
    expect(call[1]).toBeNull();
    expect(call[2]).toHaveLength(1);
    expect(call[2]?.[0]).toMatchObject({
      target: "field",
      kind: "hard",
      field_key: "gpu.vram_gib",
      operator: "gte",
      value: 16,
    });
  });

  it("sends an explicit empty list when no constraint is set", async () => {
    const { submit } = spyOnSubmit();
    const wrapper = mount(Composer);

    await wrapper.find("textarea").setValue("你好");
    await wrapper.find("form.composer").trigger("submit");

    expect(submit).toHaveBeenCalledWith("你好", null, []);
  });

  it("drops a removed constraint before the next submit", async () => {
    const { submit } = spyOnSubmit();
    const wrapper = mount(Composer);

    await wrapper.find(".constraint-panel__toggle").trigger("click");
    await wrapper.find(".constraint-panel__add").trigger("click");
    await wrapper.find('input[placeholder="gpu.vram_gib"]').setValue("gpu.vram_gib");
    await wrapper.find('input[placeholder="12"]').setValue("16");
    await wrapper.find(".constraint-form__submit").trigger("click");

    await wrapper.find(".constraint-item__actions button:last-child").trigger("click");

    await wrapper.find("textarea").setValue("不限显存");
    await wrapper.find("form.composer").trigger("submit");

    expect(submit.mock.calls[0][2]).toEqual([]);
  });

  it("clears the draft after a successful submit but keeps the constraints", async () => {
    const { submit } = spyOnSubmit();
    const wrapper = mount(Composer);

    await wrapper.find(".constraint-panel__toggle").trigger("click");
    await wrapper.find(".constraint-panel__add").trigger("click");
    await wrapper.find('input[placeholder="gpu.vram_gib"]').setValue("gpu.vram_gib");
    await wrapper.find('input[placeholder="12"]').setValue("16");
    await wrapper.find(".constraint-form__submit").trigger("click");

    await wrapper.find("textarea").setValue("第一个问题");
    await wrapper.find("form.composer").trigger("submit");

    expect((wrapper.find("textarea").element as HTMLTextAreaElement).value).toBe("");
    // Constraints are conversational, not one-shot: the panel still shows them.
    expect(wrapper.find(".constraint-panel__toggle").text()).toContain("1");
  });
});
