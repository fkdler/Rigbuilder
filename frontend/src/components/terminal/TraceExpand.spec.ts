import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import TraceExpand from "./TraceExpand.vue";
import { getQueryTrace } from "@/api/queryJobs";

vi.mock("@/api/queryJobs", () => ({ getQueryTrace: vi.fn() }));

describe("TraceExpand", () => {
  it("renders persisted tool calls instead of verification claims", async () => {
    vi.mocked(getQueryTrace).mockResolvedValue({ job_id: "job", request_id: "request", agents: [{
      agent_run_id: "agent", model_id: "Qwen", status: "completed", error_code: null,
      metrics: {}, evidence_ids: ["evidence"], tools: [{ sequence: 1, tool: "query_database",
        arguments: { sql: "SELECT name FROM gpu_catalog" }, success: true, truncated: false,
        duration_ms: 12, result_summary: "3 rows" }],
    }] });
    const wrapper = mount(TraceExpand, { props: { jobId: "job" } });
    await flushPromises();
    await wrapper.get(".trace-agent__toggle").trigger("click");
    await wrapper.get(".trace-tool").trigger("click");
    expect(wrapper.text()).toContain("query_database");
    expect(wrapper.text()).toContain("SELECT name FROM gpu_catalog");
    expect(wrapper.text()).toContain("Evidence 1 条");
  });
});
