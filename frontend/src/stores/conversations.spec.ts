import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useConversationStore } from "./conversations";
import * as conversationApi from "@/api/conversations";
import * as queryApi from "@/api/queryJobs";

vi.mock("@/api/conversations", () => ({
  createConversation: vi.fn(), deleteConversation: vi.fn(),
  getConversationDetail: vi.fn(), listConversations: vi.fn(),
}));
vi.mock("@/api/queryJobs", () => ({
  createQueryJob: vi.fn(), getQueryJob: vi.fn(), cancelQueryJob: vi.fn(),
  queryEventsUrl: vi.fn(() => "/events"),
}));

class FakeEventSource {
  onerror: (() => void) | null = null;
  addEventListener() {}
  close() {}
}

const id = "11111111-1111-4111-8111-111111111111";
const now = "2026-09-10T00:00:00Z";
const detail = { id, status: "active", messages: [], jobs: [] };
const queued = {
  id: "22222222-2222-4222-8222-222222222222", request_id: "33333333-3333-4333-8333-333333333333",
  conversation_id: id, requested_mode: "auto" as const, resolved_mode: "fusion" as const,
  status: "queued" as const, result: null, last_sequence: 1, created_at: now,
};

describe("conversation store lifecycle", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.mocked(conversationApi.listConversations).mockResolvedValue([]);
    vi.mocked(conversationApi.createConversation).mockResolvedValue(detail);
    vi.mocked(conversationApi.getConversationDetail).mockResolvedValue(detail);
    vi.mocked(queryApi.createQueryJob).mockResolvedValue(queued);
    vi.mocked(queryApi.getQueryJob).mockResolvedValue(queued);
  });

  it("ignores legacy modes and always submits auto", async () => {
    localStorage.setItem("rigbuilder.recommendation.mode", "fusion");
    const store = useConversationStore();
    expect(store.currentMode).toBe("auto");
    store.setMode("chat");
    await store.submit("推荐显卡", null, [], "fusion");
    expect(queryApi.createQueryJob).toHaveBeenCalledWith(expect.objectContaining({ mode: "auto" }));
    store.resetForSignOut();
  });

  it("creates a server-owned conversation before the first job", async () => {
    const store = useConversationStore();
    await store.submit("推荐一款显卡", null);
    expect(conversationApi.createConversation).toHaveBeenCalledOnce();
    expect(queryApi.createQueryJob).toHaveBeenCalledWith(expect.objectContaining({ conversation_id: id }));
    expect(store.currentConversationId).toBe(id);
  });

  it("keeps local state when server deletion fails", async () => {
    vi.mocked(conversationApi.listConversations).mockResolvedValue([{
      id, status: "active", title: "测试", last_message_summary: "测试", updated_at: now,
      running_job: false, latest_job_status: "completed", active_job_id: null,
    }]);
    vi.mocked(conversationApi.deleteConversation).mockRejectedValue(new Error("active job"));
    const store = useConversationStore();
    await store.loadConversations();
    await expect(store.removeConversation(id)).rejects.toThrow("active job");
    expect(store.conversations).toHaveLength(1);
    expect(store.globalError?.code).toBe("conversation_delete_failed");
  });

  it("starts fresh when a saved conversation has been cleared on the server", async () => {
    vi.mocked(conversationApi.getConversationDetail).mockRejectedValue({ isAxiosError: true, response: { status: 404 } });
    const store = useConversationStore();
    await store.selectConversation(id);
    expect(store.currentConversationId).toBeNull();
    expect(localStorage.getItem("rigbuilder.current_conversation_id")).toBeNull();
    expect(store.globalError).toBeNull();
  });

  it("ignores a previous identity's late conversation list", async () => {
    let finish!: (value: Awaited<ReturnType<typeof conversationApi.listConversations>>) => void;
    vi.mocked(conversationApi.listConversations).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
    const store = useConversationStore();
    const pending = store.loadConversations();
    store.resetForSignOut();
    finish([{ id, status: "active", title: "private old account", last_message_summary: "old", updated_at: now,
      running_job: false, latest_job_status: "completed", active_job_id: null }]);
    await pending;
    expect(store.conversations).toEqual([]);
  });

  it("ignores a previous identity's late conversation detail", async () => {
    let finish!: (value: typeof detail) => void;
    vi.mocked(conversationApi.getConversationDetail).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
    const store = useConversationStore();
    const pending = store.selectConversation(id);
    store.resetForSignOut();
    finish(detail);
    await pending;
    expect(store.conversationDetails.size).toBe(0);
    expect(store.currentConversationId).toBeNull();
  });
});
