import { expect, test, type Page } from "@playwright/test";

import { fulfillAuth, seedSession } from "./session";

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const JOB = "33333333-3333-4333-8333-333333333333";
const CANDIDATE = "44444444-4444-4444-8444-444444444444";
const NOW = "2026-09-10T00:00:00Z";

test("单行思考提示显示真实流式用量且不显示内部文本", async ({ page }, testInfo) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() => {
    localStorage.clear();
    (window as any).progressSources = [];
    (window as any).EventSource = class {
      listeners: Record<string, (event: any) => void> = {};
      onerror = null;
      constructor() { (window as any).progressSources.push(this); }
      addEventListener(name: string, fn: (event: any) => void) { this.listeners[name] = fn; }
      close() {}
    };
  });
  await mockApi(page, { running: true });
  await page.route(`**/api/conversations/${A}`, route => route.fulfill({ json: {
    id: A, status: "active", messages: [], jobs: [{ job_id: JOB, status: "running", request_message: "能否进一步解释一下为什么这样推荐？",
      resolved_mode: "fusion", created_at: NOW, result: null }],
  } }));
  await page.route(`**/api/query/jobs/${JOB}`, route => route.fulfill({ json: {
    id: JOB, request_id: "request", conversation_id: A, requested_mode: "auto", resolved_mode: "fusion", status: "running", result: null, last_sequence: 0, created_at: NOW,
  } }));
  await page.goto("/");
  await page.getByRole("button", { name: "打开会话：A 会话" }).click();
  const progress = page.locator(".work-progress");
  await expect(progress).toContainText("Cooking");
  await expect(progress).not.toContainText("tokens");
  const send = async (sequence: number, event_type: string, detail = {}) => page.evaluate(({ sequence, event_type, detail }) => {
    (window as any).progressSources.at(-1).listeners.execution({ data: JSON.stringify({
      sequence, event_type, detail, phase: "tool", status: "running", title: "agent-a SELECT secret", summary: "SQL Traceback internal error", created_at: "2026-09-15T00:00:00Z",
    }) });
  }, { sequence, event_type, detail });
  await send(1, "tool_started");
  await expect(progress).not.toContainText("tokens");
  await send(2, "llm_usage", { call_id: "a", completion_tokens: 486 });
  await expect(progress).toContainText("↑ 486 tokens");
  await send(3, "llm_usage", { call_id: "b", completion_tokens: 100 });
  await expect(progress).toContainText("↑ 586 tokens");
  await send(4, "llm_usage_finished", { call_id: "a", completion_tokens: null });
  await expect(progress).toContainText("↑ 100 tokens");
  await expect(page.getByRole("main")).not.toContainText(/agent-a|SELECT|Traceback|internal error/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await expect(progress.locator(".work-progress__track")).toHaveCount(0);
  await expect(page.getByRole("link", { name: /模型对比|决策台 V2/ })).toHaveCount(0);
  const box = await progress.boundingBox();
  expect(box!.height).toBeLessThan(60);
  await expect(progress.locator(".thinking-spinner")).toHaveText("✻");
  await page.screenshot({ path: `../.local-logs/progress-${testInfo.project.name}.png`, fullPage: true });
  await send(5, "llm_usage_finished", { call_id: "b", completion_tokens: null });
  await expect(progress).not.toContainText("tokens");
  // A different conversation must not inherit the previous job's progress.
  await page.getByRole("button", { name: "打开会话：B 会话" }).click();
  await expect(progress).toHaveCount(0);
});

function verifiedClaim(field: string, value: number, unit: string, index: number) {
  const isPrice = field === "price";
  const claim = { claim_type: isPrice ? "price" : "fact", entity_id: CANDIDATE,
    field_key: isPrice ? null : field, value, unit, value_type: "number",
    evidence_ids: isPrice ? [] : [`evidence-${index}`], benchmark_run_ids: [], rule_refs: [] };
  return { claim, source_models: ["agent-a", "agent-b"], verification: {
    claim_index: index, claim, status: "supported", reason_code: "matched",
    canonical_value: value, canonical_unit: unit,
    valid_evidence_ids: isPrice ? [] : [`evidence-${index}`], valid_benchmark_run_ids: [], valid_rule_refs: [],
    valid_price_snapshot_ids: isPrice ? ["price-snapshot"] : [],
  } };
}

test.beforeEach(async ({ page }) => {
  page.on("pageerror", (error) => console.error("PAGE_ERROR", error.message));
});

test("模型能力建议使用准确标签", async ({ page }, testInfo) => {
  await mockApi(page, { result: { kind: "catalogue_advice", conversation_id: A, model: "", context_compressed: false,
    verification: "capability_verified", answer: "Qwen3-VL-4B-Instruct：图像输入能力证据已核验。\n官方模型卡：https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct\n还需结合显存和推理框架确认能否部署。" } });
  await page.goto("/");
  await page.getByRole("button", { name: "打开会话：A 会话" }).click();
  await expect(page.getByText("数据库能力参考 · 部署条件未核验", { exact: true })).toBeVisible();
  await expect(page.getByRole("main")).not.toContainText("快速问答由单模型");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: `../.local-logs/model-advice-${testInfo.project.name}.png`, fullPage: true });
});

function fusionResult(coverage = 2 / 3, narration = "基于已验证事实生成的回退说明。") {
  return {
    kind: "fusion", request_id: "request", conversation_id: A, status: "completed", policy_version: "fusion-v1",
    agents: [
      { agent_run_id: "a", model_id: "agent-a", response_model: "Qwen3.5 9B", status: "completed" },
      { agent_run_id: "b", model_id: "agent-b", response_model: "Qwen3.5 4B", status: "completed" },
      { agent_run_id: "c", model_id: "agent-c", response_model: "GLM 4.7 Flash", status: "failed", error_code: "llm_timeout", error: "timeout" },
    ],
    result: {
      release_key: "release", policy_version: "fusion-v1", eliminated: [],
      top_k: [{ candidate_id: CANDIDATE, candidate_type: "hardware", canonical_name: "RTX 5060 Ti",
        recommendation_score: 84, credibility: 82, fact_support: 1, proof_coverage: 1,
        information_completeness: 1, constraint_validity: 1, preference_utility: 1, consensus: coverage,
        source_models: ["agent-a", "agent-b"], source_ranks: {}, agent_scores: {}, model_confidences: {},
        constraints: [], claims: [
          verifiedClaim("price", 3299, "CNY", 0),
          verifiedClaim("gpu.vram_gib", 16, "GiB", 1),
          verifiedClaim("gpu.board_power_w", 180, "W", 2),
        ], elimination_reasons: [] }],
      trace: { policy_version: "fusion-v1", unit_policy_version: "units-v1", release_key: "release",
        participating_models: ["agent-a", "agent-b"], failed_models: ["agent-c"], configured_agent_count: 3,
        agent_coverage: coverage, formula: {}, input_summary: {}, tie_break_order: [] },
    },
    presentation: { primary: { candidate_id: CANDIDATE, name: "RTX 5060 Ti", reasons: [narration] }, evidence: ["显存 16 GiB（数据库已核验）"], caveats: [], alternatives: [] },
    natural_language: { overview: narration, per_candidate: [{ candidate_id: CANDIDATE, explanation: "适合主流游戏与本地推理。" }] },
  };
}

test("办公配置草案列齐各项且不假称核显已验证", async ({ page }, testInfo) => {
  const result: any = fusionResult();
  result.result.top_k[0].canonical_name = "Core i5-12400";
  result.result.top_k[0].claims = [];
  result.presentation = {
    primary: { candidate_id: CANDIDATE, name: "Core i5-12400", reasons: ["普通办公优先基本需求和功耗，未验证整机性能。"] },
    evidence: ["Core i5-12400：基础功耗 65 W（数据库已核验）"],
    core_build: { required_core_roles: ["cpu"], status: "draft",
      core: [{ role: "cpu", candidate_id: CANDIDATE, name: "Core i5-12400", reasons: [] }],
      supporting: [
        { role: "display", spec: "核显及主板视频输出待核验", basis: "not_in_catalogue" },
        { role: "platform", spec: "Socket LGA1700", basis: "database_derived" },
        { role: "memory", spec: "16GB（通用建议，需匹配主板）", basis: "general_advice" },
        { role: "storage", spec: "512GB NVMe SSD（通用建议）", basis: "general_advice" },
        { role: "psu", spec: "功率及型号待确认", basis: "not_in_catalogue" },
        { role: "cooler", spec: "插槽及散热能力待确认", basis: "general_advice" },
        { role: "case", spec: "按主板尺寸选择，型号待确认", basis: "general_advice" },
      ], gaps: ["缺少核显证据，购买前确认显示方案。"] },
  };
  await mockApi(page, { result });
  await page.goto("/");
  await page.getByRole("button", { name: "打开会话：A 会话" }).click();
  const panel = page.locator(".recommendation");
  await expect(panel).toContainText("配置草案");
  for (const role of ["CPU", "显示方案", "主板平台", "内存", "存储", "电源", "散热", "机箱"])
    await expect(panel).toContainText(role + "：");
  await expect(panel).not.toContainText("核心部件尚未齐全");
  await expect(panel).not.toContainText("agent-a");
  await expect(panel).toContainText("核显及主板视频输出待核验");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: `../.local-logs/office-v44-${testInfo.project.name}.png`, fullPage: true });
});

function detail(id: string, result: any, status = "completed") {
  return { id, status: "active", messages: [], jobs: [{ job_id: id === B ? "55555555-5555-4555-8555-555555555555" : JOB, status, request_message: result.kind === "catalogue_advice" ? "推荐一下适合本地部署的视觉模型" : "推荐一款甜品显卡",
    resolved_mode: result.kind === "fusion" ? "fusion" : "chat", created_at: NOW, completed_at: NOW,
    result, result_natural_language: result.natural_language ?? null, agent_coverage: result.result?.trace?.agent_coverage ?? null,
    agent_summary: [], trace_summary: result.result?.trace ?? null }] };
}

async function mockApi(page: Page, options: { running?: boolean; result?: any; deleteConflict?: boolean } = {}) {
  await seedSession(page);
  const deleted = new Set<string>();
  const result = options.result ?? fusionResult();
  let cancelCalls = 0;
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (!url.pathname.startsWith("/api/")) return route.continue();
    if (await fulfillAuth(route, url.pathname)) return;
    if (url.pathname === "/api/conversations" && route.request().method() === "GET") {
      return route.fulfill({ json: [
        { id: A, status: "active", title: "A 会话", last_message_summary: "推荐显卡", updated_at: NOW,
          running_job: Boolean(options.running), latest_job_status: options.running ? "running" : "completed", active_job_id: options.running ? JOB : null },
        { id: B, status: "active", title: "B 会话", last_message_summary: "你好", updated_at: NOW, running_job: false, latest_job_status: "completed" },
      ].filter((conversation) => !deleted.has(conversation.id)) });
    }
    if (url.pathname === `/api/conversations/${A}` && route.request().method() === "GET") return route.fulfill({ json: detail(A, result, options.running ? "running" : "completed") });
    if (url.pathname === `/api/conversations/${B}` && route.request().method() === "GET") return route.fulfill({ json: detail(B, { kind: "chat", conversation_id: B, answer: "你好", model: "fast", context_compressed: false, fusion_available: true }) });
    if (url.pathname === `/api/conversations/${A}` && route.request().method() === "DELETE" && options.deleteConflict) {
      return route.fulfill({ status: 409, json: { detail: { code: "conversation_has_active_job" } } });
    }
    if (url.pathname.startsWith("/api/conversations/") && route.request().method() === "DELETE") {
      const id = url.pathname.split("/").at(-1)!;
      deleted.add(id);
      return route.fulfill({ json: { id, status: "deleted", messages: [], jobs: [] } });
    }
    if (url.pathname === `/api/query/jobs/${JOB}/cancel`) { cancelCalls += 1; return route.fulfill({ json: {} }); }
    if (url.pathname === `/api/query/jobs/${JOB}`) return route.fulfill({ json: { id: JOB, request_id: "request", conversation_id: A, requested_mode: "fusion", resolved_mode: "fusion", status: options.running ? "running" : "completed", result, last_sequence: 0, created_at: NOW } });
    if (url.pathname.endsWith("/events")) return route.fulfill({ status: 200, contentType: "text/event-stream", body: "" });
    return route.fulfill({ status: 404, json: {} });
  });
  return { cancelCalls: () => cancelCalls };
}

test("A 运行时切换 B 不取消 A，切回仍保留任务", async ({ page }) => {
  const state = await mockApi(page, { running: true });
  await page.goto("/");
  await page.getByRole("button", { name: "打开会话：B 会话" }).click();
  await expect(page.getByRole("main", { name: "会话内容" }).getByText("你好", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "打开会话：A 会话" }).click();
  expect(state.cancelCalls()).toBe(0);
});

test("刷新后恢复完成结果", async ({ page }, testInfo) => {
  await mockApi(page);
  await page.goto("/");
  await page.evaluate((id) => localStorage.setItem("rigbuilder.current_conversation_id", id), A);
  await page.reload();
  await expect(page.getByText("RTX 5060 Ti", { exact: true })).toBeVisible();
  await expect(page.getByText("基于已验证事实生成的回退说明。")).toBeVisible();
  await page.screenshot({
    path: testInfo.project.name === "mobile" ? "../.impeccable/review/mobile.png" : "../.impeccable/review/desktop.png",
    fullPage: true,
  });
});

test("删除会话同步更新 sidebar，运行中删除冲突保持服务器真值", async ({ page }) => {
  await mockApi(page, { running: true, deleteConflict: true });
  await page.goto("/");
  await page.getByLabel("删除会话：A 会话").click();
  await expect(page.getByRole("button", { name: "打开会话：A 会话" })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("409");
  await page.getByLabel("删除会话：B 会话").click();
  await expect(page.getByRole("button", { name: "打开会话：B 会话" })).toHaveCount(0);
});

test("Narrator 失败后的 deterministic presentation 仍显示", async ({ page }) => {
  await mockApi(page, { result: fusionResult(2 / 3, "确定性回退说明。") });
  await page.goto("/");
  await page.getByRole("button", { name: "打开会话：A 会话" }).click();
  await expect(page.getByText("确定性回退说明。")).toBeVisible();
});

test("单 Agent 失败显示实际 coverage", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "打开会话：A 会话" }).click();
  await expect(page.getByText(/部分核验未完成/)).toBeVisible();
  await expect(page.getByRole("main")).not.toContainText("agent-c");
});


test("缺显卡时明确显示部分配置且不推导整机电源", async ({ page }, testInfo) => {
  const result: any = fusionResult();
  result.result.top_k[0].canonical_name = "Ryzen 5 5600F";
  result.result.top_k[0].claims = [];
  result.presentation = {
    primary: { candidate_id: CANDIDATE, name: "Ryzen 5 5600F", reasons: ["CPU 参数已核验。"] },
    evidence: ["Ryzen 5 5600F：核心数 6（数据库已核验）"],
    core_build: {
      core: [{ role: "cpu", candidate_id: CANDIDATE, name: "Ryzen 5 5600F", reasons: [] }],
      supporting: [{ role: "psu", spec: "待补齐核心部件功耗后确定", basis: "not_in_catalogue" }],
      gaps: ["本次没有通过验证的显卡候选，配置单缺少核心显卡。", "散热器与机箱需要另行决定。"],
    }, caveats: ["部分候选未通过校验。"],
  };
  await mockApi(page, { result });
  await page.goto("/");
  await page.getByRole("button", { name: "打开会话：A 会话" }).click();
  await expect(page.getByText("已核验的部分配置", { exact: false })).toBeVisible();
  await expect(page.getByText("说明：", { exact: false })).toContainText("缺少核心显卡");
  await expect(page.getByText("待补齐核心部件功耗后确定", { exact: false })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  const contrast = await page.locator(".recommendation__note").evaluate((element) => {
    const rgb = (value: string) => (value.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number);
    const lum = (values: number[]) => values.map(v => v / 255).map(v => v <= 0.04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
      .reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
    let parent: Element | null = element;
    let bg = "";
    while (parent) {
      bg = getComputedStyle(parent).backgroundColor;
      if (bg !== "rgba(0, 0, 0, 0)" && bg !== "transparent") break;
      parent = parent.parentElement;
    }
    const fgL = lum(rgb(getComputedStyle(element).color));
    const bgL = lum(rgb(bg));
    return (Math.max(fgL, bgL) + .05) / (Math.min(fgL, bgL) + .05);
  });
  expect(contrast).toBeGreaterThanOrEqual(4.5);
  await page.screenshot({ path: `../.local-logs/partial-build-${testInfo.project.name}.png`, fullPage: true });
});
