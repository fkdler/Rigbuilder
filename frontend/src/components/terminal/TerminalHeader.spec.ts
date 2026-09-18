import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia, type Pinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as adminApi from "@/api/admin";
import * as conversationApi from "@/api/conversations";
import * as queryApi from "@/api/queryJobs";
import { useAuthStore } from "@/stores/auth";
import { useConversationStore } from "@/stores/conversations";
import TerminalHeader from "./TerminalHeader.vue";

vi.mock("@/api/admin", () => ({ fetchEndpoints: vi.fn(), fetchAdminSummary: vi.fn() }));
vi.mock("@/api/conversations", () => ({
  createConversation: vi.fn(), deleteConversation: vi.fn(),
  getConversationDetail: vi.fn(), listConversations: vi.fn(),
}));
vi.mock("@/api/queryJobs", () => ({
  createQueryJob: vi.fn(), getQueryJob: vi.fn(), cancelQueryJob: vi.fn(),
  queryEventsUrl: vi.fn(() => "/events"),
}));

const fetchEndpoints = vi.mocked(adminApi.fetchEndpoints);

function profile(id: string, reachable: boolean, latency: number | null = 12, error: string | null = null) {
  return {
    profile_id: id, endpoint_url: `http://127.0.0.1:808${id.slice(-1)}/v1`, configured_model_id: id,
    reachable, latency_ms: latency, error, loaded_models: [id], capability_state: "unverified",
    context_size: 16384, max_tool_rounds: 6, max_sql_calls: 6, terminal_tool_supported: false, is_local: true,
  };
}

function endpointsResult(profiles: ReturnType<typeof profile>[]) {
  return {
    ok: true as const,
    data: {
      generated_at: "2026-09-13T00:00:00Z", elapsed_ms: 23, profiles,
      distinct_endpoints: true, distinct_model_ids: true, configuration_issues: [],
      parallel_capable: true, warnings: [], agent_terminal_tool_supported: false,
      agent_parallel_required: false, capability_smoke_on_startup: false,
      agent_max_rounds: 6, agent_max_sql_calls: 6,
    },
  };
}

// The store the component reads must be the same instance the test mutates, so the pinia
// created per test is handed to mount() rather than a second one being created there.
let pinia: Pinia;

/**
 * The endpoint probe now requires an account, so the header tests run signed in.
 * `$patch` is enough: the store has no user setter on purpose, because a session may
 * only be established from a server response.
 */
function signInForProbe(): void {
  useAuthStore().$patch({
    user: {
      id: "11111111-1111-4111-8111-111111111111", username: "tester", role: "user",
      created_at: "2026-09-16T00:00:00Z", last_login_at: null,
    },
  });
}

async function mountHeader() {
  signInForProbe();
  const wrapper = mount(TerminalHeader, { global: { plugins: [pinia] } });
  await flushPromises();
  return wrapper;
}

describe("TerminalHeader", () => {
  beforeEach(() => {
    localStorage.clear();
    pinia = createPinia();
    setActivePinia(pinia);
    vi.mocked(conversationApi.listConversations).mockResolvedValue([]);
    vi.mocked(queryApi.getQueryJob).mockResolvedValue({} as never);
    fetchEndpoints.mockReset();
    fetchEndpoints.mockResolvedValue(endpointsResult([profile("agent-a", true), profile("agent-b", true), profile("agent-c", true)]));
  });

  it("does not probe endpoints before there is an account", async () => {
    // Signed out, the probe could only ever 401: an invisible failure that would also
    // be indistinguishable from a real session expiry in the auth layer.
    const wrapper = mount(TerminalHeader, { global: { plugins: [pinia] } });
    await flushPromises();
    expect(fetchEndpoints).not.toHaveBeenCalled();
    expect(wrapper.find(".endpoint-state").attributes("data-state")).toBe("unknown");
  });

  it("shows only Auto without a mode selector", async () => {
    const wrapper = await mountHeader();
    const options = wrapper.findAll("option").map((option) => option.text());
    expect(options).toEqual([]);
    expect(wrapper.find("select").exists()).toBe(false);
    expect(wrapper.find(".mode-control").text()).toBe("AUTO");
  });

  it("reports all endpoints healthy when every profile answers", async () => {
    const wrapper = await mountHeader();
    expect(wrapper.find(".endpoint-state").attributes("data-state")).toBe("ok");
    expect(wrapper.text()).toContain("3/3 online");
    expect(wrapper.find(".endpoint-warning").exists()).toBe(false);
  });

  it("warns visibly when one endpoint is unreachable", async () => {
    // A dead llama-server used to surface only as fusion_failed after ~115 s.
    fetchEndpoints.mockResolvedValue(endpointsResult([
      profile("agent-a", true), profile("agent-b", true),
      profile("agent-c", false, null, "Connection refused"),
    ]));
    const wrapper = await mountHeader();
    expect(wrapper.find(".endpoint-state").attributes("data-state")).toBe("degraded");
    const warning = wrapper.find(".endpoint-warning");
    expect(warning.exists()).toBe(true);
    expect(warning.text()).toContain("agent-c");
    expect(warning.text()).toContain("Connection refused");
    expect(warning.attributes("role")).toBe("alert");
  });

  it("reports total failure distinctly from partial failure", async () => {
    fetchEndpoints.mockResolvedValue(endpointsResult([
      profile("agent-a", false, null, "boom"), profile("agent-b", false, null, "boom"),
    ]));
    const wrapper = await mountHeader();
    expect(wrapper.find(".endpoint-state").attributes("data-state")).toBe("down");
    expect(wrapper.text()).toContain("0/2 online");
  });

  it("stays quiet when the probe itself fails", async () => {
    // fetchEndpoints returns rather than throws; a failed probe must not become an error
    // banner, because the terminal itself may still be perfectly usable.
    fetchEndpoints.mockResolvedValue({ ok: false, error: "Network Error" });
    const wrapper = await mountHeader();
    expect(wrapper.find(".endpoint-state").attributes("data-state")).toBe("unknown");
    expect(wrapper.find(".endpoint-warning").exists()).toBe(false);
  });

  it("shows the mode the router actually resolved to", async () => {
    const store = useConversationStore();
    // A conversation id must exist first: without one `currentRunner` returns a throwaway
    // object every read, so the assignment below would be discarded.
    store.currentConversationId = "11111111-1111-4111-8111-111111111111";
    store.currentRunner.job = {
      id: "j", request_id: "r", requested_mode: "auto", resolved_mode: "chat",
      status: "completed", last_sequence: 0, created_at: "2026-09-13T00:00:00Z",
    };
    const wrapper = await mountHeader();
    expect(wrapper.find(".mode-control").attributes("title")).toBe("自动分流：chat");
  });

  it("falls back to the newest stored job for a restored conversation", async () => {
    const id = "11111111-1111-4111-8111-111111111111";
    vi.mocked(conversationApi.getConversationDetail).mockResolvedValue({
      id, status: "active", messages: [],
      jobs: [
        { job_id: "old", status: "completed", request_message: "a", resolved_mode: "chat", agent_summary: [] },
        { job_id: "new", status: "completed", request_message: "b", resolved_mode: "fusion", agent_summary: [] },
      ],
    });
    const store = useConversationStore();
    await store.selectConversation(id);
    const wrapper = await mountHeader();
    // The newest job wins, so a restored session still reports what it last resolved to.
    expect(wrapper.find(".mode-control").attributes("title")).toBe("自动分流：fusion");
  });

  it("omits the resolved readout when nothing has run yet", async () => {
    const wrapper = await mountHeader();
    expect(wrapper.find(".resolved-mode").exists()).toBe(false);
  });
});
