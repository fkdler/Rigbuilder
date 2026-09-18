import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import RecommendationBlock from "./RecommendationBlock.vue";

function candidate(id = "c1", name = "RTX 5070 Ti") {
  return {
    candidate_id: id, candidate_type: "hardware", canonical_name: name,
    recommendation_score: 84, credibility: 82, fact_support: 1, proof_coverage: 1,
    information_completeness: 1, constraint_validity: 1, preference_utility: .5,
    consensus: 1, source_models: ["agent-a"], source_ranks: { "agent-a": 1 },
    agent_scores: {}, model_confidences: {}, constraints: [], elimination_reasons: [],
    claims: [{
      claim: { claim_type: "fact", entity_id: id, field_key: "gpu.vram_gib", value: 16, evidence_ids: ["e1"], benchmark_run_ids: [], rule_refs: [] },
      verification: { status: "supported", canonical_value: 16, canonical_unit: "GiB", valid_evidence_ids: ["e1"], valid_benchmark_run_ids: [], valid_rule_refs: [], valid_price_snapshot_ids: [] },
      source_models: ["agent-a"],
    }],
  };
}

function response(extra: Record<string, unknown> = {}) {
  return {
    request_id: "r", conversation_id: "c", status: "completed", policy_version: "fusion-v1",
    agents: [{ agent_run_id: "a", model_id: "agent-a", status: "completed" }],
    result: { top_k: [candidate()], eliminated: [], trace: { agent_coverage: 1, configured_agent_count: 2 } },
    ...extra,
  } as any;
}

describe("RecommendationBlock", () => {
  it("renders both requested picks with their labels, reasons and evidence", () => {
    const value = response();
    value.result.top_k = [candidate("c1", "RTX 4060"), candidate("c2", "RTX 5090")];
    value.presentation = { selections: [
      { candidate_id: "c1", name: "RTX 4060", label: "性价比方向（待核对售价）", reasons: ["缺少当前售价。"] },
      { candidate_id: "c2", name: "RTX 5090", label: "高性能方向", reasons: ["按库内性能分级选择。"] },
    ] };
    const wrapper = mount(RecommendationBlock, { props: { response: value, jobId: "job" } });
    expect(wrapper.findAll(".configuration li")).toHaveLength(2);
    expect(wrapper.text()).toContain("高性能方向：RTX 5090");
    expect(wrapper.text()).toContain("缺少当前售价");
    expect(wrapper.text()).toContain("RTX 5090：显存");
  });
  it("shows an explicit missing-pick notice", () => {
    const value = response({ presentation: {
      selections: [{ candidate_id: "c1", name: "RTX 4060", label: "性价比方向", reasons: [] }],
      selection_notice: "你要求 2 款，目前只能提供 1 款有依据的候选。",
    } });
    const wrapper = mount(RecommendationBlock, { props: { response: value, jobId: "job" } });
    expect(wrapper.find('[role="status"]').text()).toContain("只能提供 1 款");
    expect(wrapper.findAll(".configuration li")).toHaveLength(1);
  });
  it("labels model recommendations as models without a hardware build", () => {
    const value = response();
    value.result.top_k[0] = { ...candidate("model", "Qwen3-8B"), candidate_type: "ai_model" };
    const wrapper = mount(RecommendationBlock, { props: { response: value, jobId: "job" } });
    expect(wrapper.text()).toContain("以下是为您推荐的模型");
    expect(wrapper.text()).not.toContain("以下是为您推荐的硬件");
    expect(wrapper.text()).not.toContain("心仪配置");
    expect(wrapper.find(".hardware-name").text()).toBe("Qwen3-8B");
  });
  it("keeps office display uncertainty explicit without requiring a discrete GPU", () => {
    const value = response({ presentation: { core_build: {
      required_core_roles: ["cpu"], status: "draft",
      core: [{ role: "cpu", candidate_id: "c1", name: "Office CPU", reasons: [] }],
      supporting: [
        { role: "display", spec: "核显及主板视频输出待核验", basis: "not_in_catalogue" },
        { role: "storage", spec: "512GB SSD（通用建议）", basis: "general_advice" },
        { role: "cooler", spec: "型号待确认", basis: "general_advice" },
        { role: "case", spec: "型号待确认", basis: "general_advice" },
      ], gaps: ["缺少核显证据"] },
    } });
    const wrapper = mount(RecommendationBlock, { props: { response: value, jobId: "job" } });
    expect(wrapper.text()).toContain("以下是为您推荐的硬件：");
    expect(wrapper.text()).toContain("显示方案：核显及主板视频输出待核验");
    expect(wrapper.text()).not.toMatch(/存储|散热|机箱/);
    expect(wrapper.find(".hardware-name").text()).toBe("Office CPU");
    expect(wrapper.text()).not.toContain("核心部件尚未齐全");
  });
  it("shows one recommendation, reasons and verified evidence without internal Agent details", () => {
    const wrapper = mount(RecommendationBlock, {
      props: {
        response: response({
          presentation: {
            headline: "首选 RTX 5070 Ti。",
            primary: { candidate_id: "c1", name: "RTX 5070 Ti", reasons: ["16 GiB 显存适合本地模型负载。"] },
            evidence: ["RTX 5070 Ti：显存 16 GiB"],
          },
        }),
        jobId: "job",
      },
    });

    const text = wrapper.text();
    expect(text).toContain("以下是为您推荐的硬件：RTX 5070 Ti");
    expect(text).toContain("推荐理由");
    expect(text).toContain("16 GiB 显存适合本地模型负载");
    expect(text).toContain("证据");
    expect(text).not.toContain("数据库已核验");
    expect(text).not.toContain('仅构成单方面建议');
    expect(text).not.toContain("agent-a");
    expect(text).not.toContain("score");
    expect(text).not.toContain("execution trace");
  });

  it("builds a complete configuration sentence for whole-machine results", () => {
    const wrapper = mount(RecommendationBlock, {
      props: {
        response: response({
          presentation: {
            headline: "整机核心配置。",
            primary: { candidate_id: "c1", name: "RTX 5070 Ti", reasons: ["适合游戏。"] },
            core_build: {
              core: [
                { role: "gpu", candidate_id: "c1", name: "RTX 5070 Ti", reasons: [] },
                { role: "cpu", candidate_id: "c2", name: "Ryzen 7 9800X3D", reasons: [] },
              ],
              supporting: [
                { role: "platform", spec: "Socket AM5", basis: "database_derived" },
                { role: "memory", spec: "DDR5-5600", basis: "database_requirement" },
              ], gaps: [],
            },
          },
        }),
        jobId: "job",
      },
    });

    expect(wrapper.findAll(".configuration li").map(item => item.text())).toEqual(["GPU：RTX 5070 Ti", "CPU：Ryzen 7 9800X3D", "主板平台：Socket AM5", "内存：DDR5-5600"]);
  });

  it("derives a readable evidence line for older stored results", () => {
    const wrapper = mount(RecommendationBlock, { props: { response: response(), jobId: "job" } });
    expect(wrapper.text()).toContain("RTX 5070 Ti：显存 16 GiB");
  });

  it("does not render runner-up candidates or model identities", () => {
    const value = response();
    value.result.top_k.push(candidate("c2", "RX 9070 XT"));
    const wrapper = mount(RecommendationBlock, { props: { response: value, jobId: "job" } });
    expect(wrapper.text()).toContain("RTX 5070 Ti");
    expect(wrapper.text()).not.toContain("RX 9070 XT");
    expect(wrapper.text()).not.toContain("agent-a");
  });
  it("shows missing GPU before coverage caveats and labels the build as partial", () => {
    const value = response({ presentation: {
      core_build: { core: [{ role: "cpu", candidate_id: "c2", name: "Ryzen 5 5600F", reasons: [] }],
        supporting: [{ role: "psu", spec: "待补齐核心部件功耗后确定", basis: "not_in_catalogue" }],
        gaps: ["本次缺少显卡。", "机箱未收录。"] },
      caveats: ["部分结果未参与融合。", "另有候选排除。"],
    } });
    const wrapper = mount(RecommendationBlock, { props: { response: value, jobId: "job" } });
    expect(wrapper.text()).toContain("以下是为您推荐的硬件：");
    expect(wrapper.text()).toContain("本次缺少显卡");
    expect(wrapper.text()).toContain("待补齐核心部件功耗后确定");
    expect(wrapper.text()).not.toContain("以下是为您推荐的配置");
  });

});

it("renders database citations while rejecting executable URLs", () => {
  const wrapper = mount(RecommendationBlock, { props: { jobId: "job", response: response({ presentation: {
    sources: [{title:"Hardware specification", url:"https://example.org/spec"}, {title:"bad", url:"javascript:alert(1)"}],
  } }) } });
  expect(wrapper.findAll("a")).toHaveLength(1);
  expect(wrapper.find("a").attributes("href")).toBe("https://example.org/spec");
  expect(wrapper.find("a").attributes("rel")).toContain("noopener");
});
