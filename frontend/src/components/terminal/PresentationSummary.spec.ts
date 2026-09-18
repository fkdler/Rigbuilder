import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import PresentationSummary from "./PresentationSummary.vue";

const FULL = {
  headline: "预算 5000 元内最值得买的是 GeForce RTX 4060。",
  primary: {
    candidate_id: "c1",
    name: "GeForce RTX 4060",
    reasons: [
      "价格 2599 CNY，8GB 显存够跑 1080p 网游。",
      "115W 功耗对 450W 电源友好。",
    ],
  },
  alternatives: [{ candidate_id: "c2", name: "RTX 4070", note: "贵 1200 CNY，但显存更大。" }],
  caveats: ["有 1 个模型未参与本次融合，覆盖度受限。"],
};

describe("PresentationSummary", () => {
  it("renders the headline, the primary's reasons, the alternatives and the caveats", () => {
    const wrapper = mount(PresentationSummary, { props: { presentation: FULL } });
    const text = wrapper.text();

    expect(text).toContain(FULL.headline);
    expect(text).toContain("GeForce RTX 4060");
    for (const reason of FULL.primary.reasons) expect(text).toContain(reason);
    expect(text).toContain("贵 1200 CNY，但显存更大。");
    expect(text).toContain("有 1 个模型未参与本次融合，覆盖度受限。");
  });

  it("puts the conclusion above the ranked detail", () => {
    // The measured complaint was that the result was "僵硬地按排名返回": the list
    // came first and the answer was never framed as an answer.
    const wrapper = mount(PresentationSummary, { props: { presentation: FULL } });
    const text = wrapper.text();
    expect(text.indexOf(FULL.headline)).toBeLessThan(text.indexOf("GeForce RTX 4060"));
    expect(text.indexOf(FULL.headline)).toBeLessThan(text.indexOf("RTX 4070"));
  });

  it("renders a headline-only conclusion when there is nothing to recommend", () => {
    const wrapper = mount(PresentationSummary, {
      props: { presentation: { headline: "本次没有候选通过数据库事实校验。" } },
    });
    expect(wrapper.text()).toContain("本次没有候选通过数据库事实校验。");
    expect(wrapper.find(".presentation__primary").exists()).toBe(false);
    expect(wrapper.find(".presentation__caveats").exists()).toBe(false);
  });

  it("omits the alternatives and caveat groups when they are empty", () => {
    const wrapper = mount(PresentationSummary, {
      props: {
        presentation: {
          headline: "结论。",
          primary: { candidate_id: "c1", name: "X", reasons: ["理由。"] },
          alternatives: [],
          caveats: [],
        },
      },
    });
    expect(wrapper.text()).not.toContain("备选");
    expect(wrapper.text()).not.toContain("本结论的局限");
  });
});
