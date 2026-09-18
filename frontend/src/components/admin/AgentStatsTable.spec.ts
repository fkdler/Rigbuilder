import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import type { AgentStats, AgentStatsResponse, Percentiles } from "@/api/admin";
import AgentStatsTable from "./AgentStatsTable.vue";
import { MISSING } from "./format";

const percentiles = (p50: number | null, p95: number | null = p50, maximum = p50): Percentiles => ({
  avg: p50, p50, p95, maximum,
});

function agent(overrides: Partial<AgentStats> = {}): AgentStats {
  return {
    model_id: "agent-a", runs: 10, completed: 8, failed: 2, completion_rate: 0.8,
    truth_verified: 8, truth_verified_rate: 0.8, errors: {},
    rounds: percentiles(3), sql_calls: percentiles(4), llm_calls: percentiles(3), tool_calls: percentiles(6),
    wall_clock_ms: percentiles(86_132), total_duration_ms: percentiles(84_000),
    agent_duration_ms: percentiles(83_000), llm_duration_ms: percentiles(80_000),
    prompt_eval_duration_ms: percentiles(2_000), generation_duration_ms: percentiles(78_000),
    queue_duration_ms: percentiles(120), verification_duration_ms: percentiles(300),
    fusion_duration_ms: percentiles(50),
    tokens_per_second: 12.5, tokens_per_second_coverage: 1,
    first_run_at: null, last_run_at: null, observed_span_hours: null,
    prompt_tokens: 1_000, completion_tokens: 500, total_tokens: 1_500,
    llm_duration_total_ms: 80_000, tool_duration_total_ms: 400,
    context_pruned_messages: 2, duplicate_statements_rejected: 1,
    runs_with_usable_candidate_id: 9, protocol_retries: 0,
    metric_coverage: {},
    ...overrides,
  };
}

function response(agents: AgentStats[], byDay: AgentStatsResponse["by_day"] = []): AgentStatsResponse {
  return {
    window_hours: 0, since: null, model_filter: null, generated_at: "2026-09-13T00:00:00Z",
    elapsed_ms: 31, total_runs: agents.reduce((sum, item) => sum + item.runs, 0),
    agents, by_error_code: {}, by_day: byDay, recent_runs: [], parallel_runs: 10, serial_runs: 0,
  };
}

/** Charts are stubbed: this suite asserts what the table states, not how it draws. */
const mountTable = (stats: AgentStatsResponse) =>
  mount(AgentStatsTable, { props: { stats }, global: { stubs: { ChartPanel: true } } });

describe("AgentStatsTable", () => {
  it("shows p50, p95 and max per Agent", () => {
    const wrapper = mount(AgentStatsTable, {
      props: {
        stats: response([agent({
          rounds: { avg: 3, p50: 3, p95: 5, maximum: 6 },
          wall_clock_ms: { avg: 1, p50: 86_132, p95: 154_026, maximum: 201_905 },
        })]),
      },
    });
    const text = wrapper.text();
    expect(text).toContain("agent-a");
    expect(text).toContain("1.4 min"); // p50 wall clock
    expect(text).toContain("2.6 min"); // p95
    expect(text).toContain("3.4 min"); // max
  });

  it("renders a missing metric as the marker, never as zero", () => {
    // A run that never wrote the key reports null; printing 0 would state a measurement
    // that was never taken.
    const wrapper = mount(AgentStatsTable, {
      props: { stats: response([agent({ rounds: percentiles(null, null, null), metric_coverage: {} })]) },
    });
    const roundsRow = wrapper.findAll("tbody tr")[0];
    expect(roundsRow.text()).toContain(MISSING);
    expect(roundsRow.text()).not.toMatch(/\b0\b/);
  });

  it("keeps a genuine zero distinct from a missing value", () => {
    const wrapper = mount(AgentStatsTable, {
      props: { stats: response([agent({ rounds: percentiles(0, 0, 0), metric_coverage: { rounds_used: 10 } })]) },
    });
    const roundsRow = wrapper.findAll("tbody tr")[0];
    expect(roundsRow.text()).toContain("0.00");
    expect(roundsRow.text()).not.toContain(MISSING);
  });

  it("flags a metric that covers only part of the window and states the count", () => {
    // Measured: six of the 33 keys appear in only 107-132 of 183 historical runs.
    const wrapper = mount(AgentStatsTable, {
      props: {
        stats: response([agent({
          runs: 183,
          agent_duration_ms: percentiles(83_000),
          metric_coverage: { agent_duration_ms: 107, llm_duration_ms: 183, rounds_used: 183 },
        })]),
      },
    });
    const row = wrapper.findAll("tbody tr").find((item) => item.text().includes("agent_duration"));
    expect(row).toBeDefined();
    expect(row!.text()).toContain("107/183 runs");
    expect(row!.text()).toContain("partial");
    expect(row!.find(".coverage").attributes("data-partial")).toBe("true");
  });

  it("does not flag full coverage as partial", () => {
    const wrapper = mount(AgentStatsTable, {
      props: {
        stats: response([agent({
          runs: 183, llm_duration_ms: percentiles(80_000),
          metric_coverage: { llm_duration_ms: 183, rounds_used: 183 },
        })]),
      },
    });
    const row = wrapper.findAll("tbody tr").find((item) => item.text().includes("llm_duration"));
    expect(row!.text()).toContain("183/183 runs");
    expect(row!.text()).not.toContain("partial");
  });

  it("says n/a rather than partial when a metric has no coverage count", () => {
    // wall_clock_ms comes from the owning job's stamps, so metric_coverage has no key for it.
    const wrapper = mount(AgentStatsTable, {
      props: { stats: response([agent({ metric_coverage: {} })]) },
    });
    const row = wrapper.findAll("tbody tr").find((item) => item.text().includes("wall_clock"))!;
    expect(row.text()).toContain("n/a");
    expect(row.find(".coverage").attributes("data-partial")).toBe("false");
  });

  it("withholds a throughput rate built on partial duration coverage", () => {
    // A rate computed from a subset of runs looks precise while being wrong, so the
    // backend returns null and the table must say why rather than print a number.
    const wrapper = mount(AgentStatsTable, {
      props: {
        stats: response([agent({ tokens_per_second: 12.5, tokens_per_second_coverage: 0.58 })]),
      },
    });
    expect(wrapper.text()).toContain("withheld");
    const tokens = wrapper.find(".agent-block__tokens");
    expect(tokens.text()).toContain(MISSING);
  });

  it("shows the throughput rate and its coverage when it is trustworthy", () => {
    const wrapper = mount(AgentStatsTable, {
      props: { stats: response([agent({ tokens_per_second: 12.5, tokens_per_second_coverage: 1 })]) },
    });
    expect(wrapper.text()).toContain("12.5");
    expect(wrapper.text()).toContain("100% coverage");
    expect(wrapper.text()).not.toContain("withheld");
  });

  it("renders the per-day series from its own payload", () => {
    // The day series truncates agent_run.created_at, so it is carried on the Agent stats
    // payload and not on the job payload. Reading it from the wrong one drew an empty chart
    // against the live API while the stubbed unit tests stayed green.
    const wrapper = mountTable(response([agent()], [
      { day: "2026-09-13T00:00:00+08:00", runs: 117, completed: 73, failed: 44, total_tokens: 6_655_209 },
    ]));
    expect(wrapper.text()).toContain("runs per day");
  });

  it("states the absence of a day series rather than drawing an empty axis", () => {
    expect(mountTable(response([agent()])).text()).toContain("窗口内没有按天记录。");
  });

  it("reports an empty window without inventing rows", () => {
    const wrapper = mount(AgentStatsTable, { props: { stats: response([]) } });
    expect(wrapper.text()).toContain("没有 Agent 运行记录");
    expect(wrapper.findAll("tbody tr")).toHaveLength(0);
  });

  it("states whether the window is bounded or all time", () => {
    const allTime = mount(AgentStatsTable, { props: { stats: response([agent()]) } });
    expect(allTime.text()).toContain("all time");
    const bounded = mount(AgentStatsTable, {
      props: { stats: { ...response([agent()]), since: "2026-09-13T00:00:00Z", model_filter: "agent-b" } },
    });
    expect(bounded.text()).toContain("filtered to agent-b");
    expect(bounded.text()).toContain("2026-09-13 00:00");
  });
});
