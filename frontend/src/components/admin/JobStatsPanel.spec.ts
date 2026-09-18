import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import type { JobStatsResponse } from "@/api/admin";
import JobStatsPanel from "./JobStatsPanel.vue";

function stats(overrides: Partial<JobStatsResponse> = {}): JobStatsResponse {
  return {
    window_hours: 0, since: null, generated_at: "2026-09-13T16:41:55Z", elapsed_ms: 31,
    total_jobs: 117,
    by_status: { completed: 87, failed: 30 },
    by_resolved_mode: { fusion: 101, chat: 16 },
    by_error_code: { "(none)": 87, fusion_failed: 27 },
    duration_ms: { avg: 74_361, p50: 86_132, p95: 154_026, maximum: 201_905 },
    coverage: { avg: 0.633, p50: 0.666, p95: 1, maximum: 1 },
    events_by_type: { llm_completed: 1_756, agent_failed: 162 },
    fusion_jobs: 101, fusion_with_zero_coverage: 0,
    recent: [{
      job_id: "e56d135a-723f-4e52-989f-49dfaaa5c61c", status: "completed", resolved_mode: "chat",
      error_code: null, duration_ms: 743, agent_coverage: null, event_count: 7, created_at: "2026-09-13T16:41:55Z",
    }],
    ...overrides,
  };
}

const mountPanel = (value: JobStatsResponse) => mount(JobStatsPanel, {
  props: { stats: value },
  // Charts are stubbed: this suite is about what the panel states, not how it draws.
  global: { stubs: { ChartPanel: true } },
});

describe("JobStatsPanel", () => {
  it("renders one chart per populated category set", () => {
    const wrapper = mountPanel(stats());
    // job status, error codes, resolved mode, event types, plus the recent-jobs table.
    expect(wrapper.findAll(".panel")).toHaveLength(5);
    expect(wrapper.text()).toContain("job status");
    expect(wrapper.text()).toContain("error codes");
    expect(wrapper.text()).toContain("resolved mode");
    expect(wrapper.text()).toContain("event types");
  });

  it("does not claim a per-day series", () => {
    // The day series truncates agent_run.created_at, so it belongs to the Agent stats
    // payload. Reading it from the job payload drew an empty chart against the live API.
    const wrapper = mountPanel(stats({ by_day: undefined as never }));
    expect(wrapper.text()).not.toContain("runs per day");
    expect(wrapper.text()).not.toContain("窗口内没有按天记录。");
  });

  it("leaves out a chart whose data is absent, with a stated reason", () => {
    const wrapper = mountPanel(stats({ by_status: {} }));
    expect(wrapper.text()).toContain("窗口内没有任务。");
    expect(wrapper.findAll(".panel")).toHaveLength(5);
  });

  it("renders an absent optional array instead of throwing", () => {
    // Regression: `stats.by_day.length` inside the render function made a payload that
    // omitted one optional array crash the entire dashboard, not just this panel. Plan
    // V3.6 §7-5 requires that no single missing field can white-screen the console.
    const withoutRecent = stats();
    delete (withoutRecent as Partial<JobStatsResponse>).recent;

    const wrapper = mountPanel(withoutRecent);
    expect(wrapper.text()).toContain("窗口内没有任务。");
    expect(wrapper.text()).toContain("job status");
  });

  it("renders absent counts maps instead of throwing", () => {
    const withoutCounts = stats({ by_status: undefined as never, by_error_code: undefined as never });
    const wrapper = mountPanel(withoutCounts);
    expect(wrapper.text()).toContain("窗口内没有任务。");
    expect(wrapper.text()).toContain("窗口内没有错误码记录。");
    expect(wrapper.text()).not.toContain("NaN");
  });

  it("reports a null coverage as missing, not as 0/3", () => {
    // The mock's chat job has agent_coverage null: not a fusion, which is a different fact
    // from a fusion that found no candidate.
    const row = mountPanel(stats()).find(".recent-table tbody tr");
    expect(row.text()).toContain("—");
    expect(row.text()).not.toContain("0/3");
  });

  it("reports a genuine zero coverage as 0/3", () => {
    const withZero = stats({
      recent: [{
        job_id: "abc12345-0000-4000-8000-000000000000", status: "completed", resolved_mode: "fusion",
        error_code: null, duration_ms: 1, agent_coverage: 0, event_count: 1, created_at: "2026-09-13T16:41:55Z",
      }],
    });
    expect(mountPanel(withZero).find(".recent-table tbody tr").text()).toContain("0/3");
  });

  it("says so when there are no recent jobs", () => {
    expect(mountPanel(stats({ recent: [] })).text()).toContain("窗口内没有任务。");
  });

  it("does not repeat the scalar figures shown in the overview bar", () => {
    // Duplicated numbers eventually disagree; the overview bar is the single source.
    const wrapper = mountPanel(stats());
    expect(wrapper.text()).not.toContain("total jobs");
    expect(wrapper.text()).not.toContain("117");
  });
});
