import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import ProductIntroduction from "./ProductIntroduction.vue";

it("keeps catalogue facts distinct from verified facts and labels citation scope", () => {
  const wrapper = mount(ProductIntroduction, { props: { result: {
    kind: "product_info", conversation_id: "c", answer: "GeForce RTX 5090D 的目录资料。",
    model: "", context_compressed: false, product_name: "GeForce RTX 5090D", verification: "partial",
    facts: [
      { field: "gpu.vram_gib", label: "显存容量", value: "32", unit: "GiB", status: "catalogue_only", evidence_ids: [] },
      { field: "gpu.architecture", label: "架构", value: "Blackwell", unit: "", status: "verified", evidence_ids: ["e"] },
    ],
    sources: [{ title: "NVIDIA", url: "https://nvidia.com/", field: "entity.canonical_name", label: "产品名称" },
      { title: "unsafe", url: "javascript:alert(1)" }],
  } } });
  expect(wrapper.text()).toContain("32 GiB");
  expect(wrapper.findAll("dd")[0].text()).not.toContain("待补证据");
  expect(wrapper.find("details").attributes("open")).toBeUndefined();
  expect(wrapper.find("details").text()).toContain("待补证据");
  expect(wrapper.find("details").text()).toContain("已核验");
  expect(wrapper.text()).toContain("产品名称");
  expect(wrapper.findAll("a")).toHaveLength(1);
  expect(wrapper.text()).not.toContain("推荐理由");
});
