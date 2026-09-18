import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { createPinia, setActivePinia } from "pinia";

import * as authApi from "@/api/auth";
import * as conversationApi from "@/api/conversations";
import AppSidebar from "./AppSidebar.vue";
import { useAuthStore } from "@/stores/auth";
import { useConversationStore } from "@/stores/conversations";

vi.mock("@/api/auth", () => ({
  authErrorMessage: vi.fn((cause: unknown) => cause instanceof Error ? cause.message : "操作失败"),
  registerAccount: vi.fn(), loginAccount: vi.fn(), logoutAccount: vi.fn(), fetchMe: vi.fn(),
  updateUsername: vi.fn(), updatePassword: vi.fn(),
}));
vi.mock("@/api/conversations", () => ({
  createConversation: vi.fn(), deleteConversation: vi.fn(), getConversationDetail: vi.fn(), listConversations: vi.fn(),
}));

const USER = { id: "11111111-1111-4111-8111-111111111111", username: "Ada", role: "user", created_at: "2026-09-16T00:00:00Z", last_login_at: null };

function render() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/", component: { template: "<div />" } }] });
  return { router, wrapper: mount(AppSidebar, { global: { plugins: [router], stubs: { AsciiLogoLarge: true } } }) };
}

async function signIn() {
  vi.mocked(authApi.loginAccount).mockResolvedValue({ token: "token-a", expires_at: "2026-10-01T00:00:00Z", user: USER });
  await useAuthStore().signIn("Ada", "pw");
}

describe("sidebar modules", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    vi.mocked(conversationApi.listConversations).mockResolvedValue([]);
  });

  it("shows account identity and both main modules", async () => {
    await signIn();
    const { wrapper } = render();
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="account-name"]').text()).toBe("Ada");
    expect(wrapper.text()).toContain("会话终端");
    expect(wrapper.text()).toContain("硬件日报");
    await wrapper.find('[data-testid="account-button"]').trigger("click");
    expect(useAuthStore().userModalOpen).toBe(true);
  });

  it("does not list sessions before sign-in", async () => {
    const { wrapper } = render();
    await wrapper.vm.$nextTick();
    expect(conversationApi.listConversations).not.toHaveBeenCalled();
    expect(wrapper.find('[data-testid="account-name"]').text()).toBe("登录 / 注册");
  });

  it("starts a new terminal session when selecting the terminal module", async () => {
    await signIn();
    const { router, wrapper } = render();
    const conversations = useConversationStore();
    conversations.currentConversationId = "existing";
    const terminalButton = wrapper.findAll("button").find((button) => button.text().includes("会话终端"));
    expect(terminalButton).toBeTruthy();
    await terminalButton!.trigger("click");
    await router.isReady();
    expect(conversations.currentConversationId).toBeNull();
    expect(router.currentRoute.value.query.module).toBe("terminal");
  });

  it("shows status and date metadata without the conversation summary", async () => {
    vi.mocked(conversationApi.listConversations).mockResolvedValue([{
      id: "c1", title: "装机建议", last_message_summary: "这行摘要不显示",
      updated_at: "2026-09-18T12:00:00Z", latest_job_status: "completed",
      active_job_id: null, running_job: false, status: "active",
    }]);
    await signIn();
    const { wrapper } = render();
    await flushPromises();

    expect(wrapper.get(".session-title").text()).toBe("装机建议");
    expect(wrapper.find(".session-summary").exists()).toBe(false);
    expect(wrapper.get(".session-meta").text()).toContain("done");
  });
});
