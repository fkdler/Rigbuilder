import { beforeEach, describe, expect, it, vi } from "vitest";

import { http } from "./http";
import {
  fetchAdminSummary, fetchAgentStats, fetchDatabaseOverview, fetchEndpoints, fetchJobStats,
} from "./admin";

vi.mock("./http", () => ({ http: { get: vi.fn() } }));
const get = vi.mocked(http.get);

describe("admin api client", () => {
  beforeEach(() => {
    get.mockReset();
    get.mockResolvedValue({ data: { ok: "payload" } });
  });

  it("calls the five documented paths", async () => {
    await fetchAdminSummary();
    await fetchAgentStats();
    await fetchJobStats();
    await fetchDatabaseOverview();
    await fetchEndpoints();
    expect(get.mock.calls.map(([path]) => path)).toEqual([
      "/api/admin/summary",
      "/api/admin/stats/agents",
      "/api/admin/stats/jobs",
      "/api/admin/database",
      "/api/admin/endpoints",
    ]);
  });

  it("omits unset query params so the server defaults apply", async () => {
    await fetchAdminSummary();
    expect(get.mock.calls[0][1]?.params).toEqual({});
  });

  it("maps windowHours, modelId and limit onto snake_case params", async () => {
    await fetchAgentStats({ windowHours: 0, modelId: "agent-b", limit: 50 });
    expect(get.mock.calls[0][1]?.params).toEqual({
      window_hours: 0, model_id: "agent-b", limit: 50,
    });
  });

  it("passes windowHours 0 through rather than treating it as unset", async () => {
    // 0 is meaningful: the backend reads <= 0 as "all time", which is the default the
    // dashboard opens with.  A falsy check here would silently narrow the window.
    await fetchJobStats({ windowHours: 0 });
    expect(get.mock.calls[0][1]?.params).toEqual({ window_hours: 0 });
  });

  it("returns failures instead of throwing, so one dead endpoint cannot break the page", async () => {
    get.mockRejectedValue(new Error("Network Error"));
    const result = await fetchAdminSummary();
    expect(result).toEqual({ ok: false, error: "Network Error" });
  });

  it("wraps non-Error rejections into a readable string", async () => {
    get.mockRejectedValue("boom");
    expect(await fetchAdminSummary()).toEqual({ ok: false, error: "boom" });
  });

  it("gives the endpoint probe a bounded timeout", async () => {
    await fetchEndpoints(1_234);
    expect(get.mock.calls[0][1]?.timeout).toBe(1_234);
  });

  it("passes no timeout override for the heavier summary call", async () => {
    await fetchAdminSummary();
    expect(get.mock.calls[0][1]?.timeout).toBeUndefined();
  });
});
