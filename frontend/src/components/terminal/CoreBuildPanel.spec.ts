import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import CoreBuildPanel from "./CoreBuildPanel.vue";
import type { CoreBuild } from "@/api/agentFusion";

/**
 * A core build must never read like a verified recommendation outside `core`.
 *
 * The Catalogue carries no compatibility relation, so every supporting row is either a
 * requirement the Release states or a backend derivation.  The badge that says which is
 * the whole point of the panel, so it is asserted rather than assumed.
 */
const FULL: CoreBuild = {
  core: [
    { role: "gpu", candidate_id: "g1", name: "GeForce RTX 5070 Ti", reasons: [] },
    { role: "cpu", candidate_id: "c1", name: "Ryzen 7 9800X3D", reasons: [] },
  ],
  supporting: [
    { role: "platform", spec: "Socket AM5", basis: "database_derived", note: "依据 CPU socket AM5 推导。" },
    { role: "memory", spec: "DDR5，可用档位：DDR5-4800 (UDIMM)", basis: "database_derived", note: null },
    { role: "storage", spec: "未收录", basis: "not_in_catalogue", note: "数据库未收录本项目的存储需求。" },
  ],
  gaps: ["散热器与机箱数据本项目未收录，需要用户另行决定。"],
};

describe("CoreBuildPanel", () => {
  it("names every core component in order", () => {
    const wrapper = mount(CoreBuildPanel, { props: { build: FULL } });
    const names = wrapper.findAll(".core-part__name").map((node) => node.text());
    expect(names).toEqual(["GeForce RTX 5070 Ti", "Ryzen 7 9800X3D"]);
  });

  it("labels each supporting row with where it came from", () => {
    const wrapper = mount(CoreBuildPanel, { props: { build: FULL } });
    const badges = wrapper.findAll(".support-row__basis").map((node) => node.text());
    expect(badges).toContain("规则推导");
    expect(badges).toContain("未收录");
  });

  it("styles an uncovered part differently from a derived one", () => {
    const wrapper = mount(CoreBuildPanel, { props: { build: FULL } });
    expect(wrapper.find(".support-row__basis--not_in_catalogue").exists()).toBe(true);
    expect(wrapper.find(".support-row__basis--database_derived").exists()).toBe(true);
  });

  it("surfaces what the catalogue cannot decide", () => {
    const wrapper = mount(CoreBuildPanel, { props: { build: FULL } });
    expect(wrapper.text()).toContain("散热器与机箱数据本项目未收录");
  });

  it("omits the gaps list when there is nothing to disclose", () => {
    const wrapper = mount(CoreBuildPanel, { props: { build: { ...FULL, gaps: [] } } });
    expect(wrapper.find(".core-build__gaps").exists()).toBe(false);
  });

  it("renders a partial build without core components crashing", () => {
    const wrapper = mount(CoreBuildPanel, {
      props: { build: { core: [], supporting: [FULL.supporting[0]], gaps: ["缺少核心显卡。"] } },
    });
    expect(wrapper.find(".core-build__core").exists()).toBe(false);
    expect(wrapper.text()).toContain("缺少核心显卡");
  });
});
