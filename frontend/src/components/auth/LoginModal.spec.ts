import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import * as authApi from "@/api/auth";
import * as conversationApi from "@/api/conversations";
import LoginModal from "./LoginModal.vue";
import { useAuthStore } from "@/stores/auth";

vi.mock("@/api/auth", () => ({
  authErrorMessage: vi.fn((cause: unknown) => (cause instanceof Error ? cause.message : "操作失败，请重试。")),
  registerAccount: vi.fn(),
  loginAccount: vi.fn(),
  logoutAccount: vi.fn(),
  fetchMe: vi.fn(),
  updateUsername: vi.fn(),
  updatePassword: vi.fn(),
}));
vi.mock("@/api/conversations", () => ({
  createConversation: vi.fn(), deleteConversation: vi.fn(),
  getConversationDetail: vi.fn(), listConversations: vi.fn(),
}));

const USER = {
  id: "11111111-1111-4111-8111-111111111111",
  username: "Ada", role: "user", created_at: "2026-09-16T00:00:00Z", last_login_at: null,
};
const SESSION = { token: "token-a", expires_at: "2026-10-01T00:00:00Z", user: USER };

function render() {
  // Teleport is stubbed so the overlay is asserted in place; the real component
  // teleports to <body> only to escape the sidebar's stacking context.
  return mount(LoginModal, { global: { stubs: { teleport: true } } });
}

describe("login modal", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    vi.mocked(authApi.loginAccount).mockResolvedValue(SESSION);
    vi.mocked(authApi.registerAccount).mockResolvedValue(SESSION);
    vi.mocked(conversationApi.listConversations).mockResolvedValue([]);
  });

  it("renders as a dialog overlay rather than a page", () => {
    const wrapper = render();
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true);
    expect(wrapper.find('h2').text()).toContain("登录");
    expect(wrapper.find('button[aria-label="关闭"]').exists()).toBe(true);
  });

  it("asks for a username instead of calling the API with an empty form", async () => {
    const wrapper = render();
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(wrapper.find('[data-testid="auth-error"]').text()).toContain("请输入用户名");
    expect(authApi.loginAccount).not.toHaveBeenCalled();
  });

  it("signs in with the typed credentials", async () => {
    const wrapper = render();
    await wrapper.find('[data-testid="auth-username"]').setValue("Ada");
    await wrapper.find('[data-testid="auth-password"]').setValue("pw");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(authApi.loginAccount).toHaveBeenCalledWith("Ada", "pw");
    expect(useAuthStore().isAuthenticated).toBe(true);
  });

  it("shows the server's rejection reason", async () => {
    vi.mocked(authApi.loginAccount).mockRejectedValue(new Error("用户名或密码不正确。"));
    const wrapper = render();
    await wrapper.find('[data-testid="auth-username"]').setValue("Ada");
    await wrapper.find('[data-testid="auth-password"]').setValue("wrong");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(wrapper.find('[data-testid="auth-error"]').text()).toBe("用户名或密码不正确。");
    expect(useAuthStore().isAuthenticated).toBe(false);
  });

  it("switches to registration and creates the account", async () => {
    const wrapper = render();
    await wrapper.findAll('[role="tab"]')[1].trigger("click");
    await wrapper.find('[data-testid="auth-username"]').setValue("Bob");
    await wrapper.find('[data-testid="auth-password"]').setValue("pw");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(authApi.registerAccount).toHaveBeenCalledWith("Bob", "pw");
    expect(useAuthStore().isAuthenticated).toBe(true);
  });
});
