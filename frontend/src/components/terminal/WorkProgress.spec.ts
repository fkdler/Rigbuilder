import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import WorkProgress from "./WorkProgress.vue";

describe("single-line thinking status", () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(10000); });
  afterEach(() => { vi.useRealTimers(); });

  it("shows actual elapsed time without fabricated usage and cleans up on unmount", async () => {
    const wrapper = mount(WorkProgress, { props: { status: "running", startedAt: 7000 } });
    expect(wrapper.text()).toContain("Cooking");
    expect(wrapper.text()).toContain("(3s)");
    expect(wrapper.text()).not.toContain("tokens");
    await vi.advanceTimersByTimeAsync(1000);
    expect(wrapper.text()).toContain("(4s)");
    wrapper.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("formats only real positive completion usage", async () => {
    const wrapper = mount(WorkProgress, { props: { status: "running", startedAt: 10000 } });
    for (const count of [undefined, null, 0, -1, NaN]) {
      await wrapper.setProps({ completionTokens: count });
      expect(wrapper.text()).not.toContain("tokens");
    }
    for (const [count, label] of [[842, "842"], [1200, "1.2k"], [1284, "1.3k"], [12400, "12.4k"]] as const) {
      await wrapper.setProps({ completionTokens: count });
      expect(wrapper.text()).toContain(`${label} tokens`);
    }
    wrapper.unmount();
  });

  it.each(["completed", "cancel_requested", "cancelled", "failed", "idle"])("stops timers on %s", async status => {
    const wrapper = mount(WorkProgress, { props: { status: "running", startedAt: 10000 } });
    await wrapper.setProps({ status });
    expect(wrapper.find('[role="status"]').exists()).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
    wrapper.unmount();
  });
});
