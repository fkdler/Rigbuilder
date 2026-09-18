import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import * as authApi from "@/api/auth";
import * as conversationApi from "@/api/conversations";
import UserInfoModal from "./UserInfoModal.vue";
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

async function signedIn() {
  const store = useAuthStore();
  vi.mocked(authApi.loginAccount).mockResolvedValue({
    token: "token-a", expires_at: "2026-10-01T00:00:00Z", user: USER,
  });
  await store.signIn("Ada", "pw");
  store.openUserModal();
  return store;
}

function render() {
  return mount(UserInfoModal, { global: { stubs: { teleport: true } } });
}

describe("account panel", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    vi.mocked(authApi.logoutAccount).mockResolvedValue(undefined);
    vi.mocked(authApi.updateUsername).mockResolvedValue({ ...USER, username: "Adalovelace" });
    vi.mocked(authApi.updatePassword).mockResolvedValue(undefined);
    vi.mocked(conversationApi.listConversations).mockResolvedValue([]);
  });

  it("only offers sign-out to administrators", async () => {
    const store = await signedIn();
    store.user = { ...USER, role: "admin" };
    const wrapper = render();
    expect(wrapper.find("form").exists()).toBe(false);
    expect(wrapper.find('[data-testid="current-username"]').text()).toBe("Ada");
    await wrapper.find('[data-testid="sign-out"]').trigger("click");
    await flushPromises();
    expect(store.isAuthenticated).toBe(false);
  });

  it("shows the signed-in username", async () => {
    await signedIn();
    const wrapper = render();
    expect(wrapper.find('[data-testid="current-username"]').text()).toBe("Ada");
  });

  it("changes the username from the authenticated session", async () => {
    await signedIn();
    const wrapper = render();

    await wrapper.find('[data-testid="new-username"]').setValue("Adalovelace");
    await wrapper.find('[data-testid="username-form"]').trigger("submit");
    await flushPromises();

    expect(authApi.updateUsername).toHaveBeenCalledWith("Adalovelace");
    expect(wrapper.find('[data-testid="username-current-password"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="account-notice"]').text()).toContain("用户名已更新");
  });

  it.skip("submits a username change without a password input", async () => {
    await signedIn();
    const wrapper = render();

    await wrapper.find('[data-testid="new-username"]').setValue("Adalovelace");
    await wrapper.find('[data-testid="username-form"]').trigger("submit");

    expect(authApi.updateUsername).toHaveBeenCalledWith("Adalovelace");
    expect(wrapper.find('[data-testid="account-error"]').text()).toContain("当前密码");
  });

  it("changes the password and says other devices were signed out", async () => {
    await signedIn();
    const wrapper = render();

    await wrapper.find('[data-testid="current-password"]').setValue("pw");
    await wrapper.find('[data-testid="new-password"]').setValue("new-pw");
    await wrapper.find('[data-testid="password-form"]').trigger("submit");
    await flushPromises();

    expect(authApi.updatePassword).toHaveBeenCalledWith("pw", "new-pw");
    expect(wrapper.find('[data-testid="account-notice"]').text()).toContain("其他设备");
  });

  it("reports a wrong current password", async () => {
    vi.mocked(authApi.updatePassword).mockRejectedValue(new Error("当前密码不正确。"));
    await signedIn();
    const wrapper = render();

    await wrapper.find('[data-testid="current-password"]').setValue("wrong");
    await wrapper.find('[data-testid="new-password"]').setValue("new-pw");
    await wrapper.find('[data-testid="password-form"]').trigger("submit");
    await flushPromises();

    expect(wrapper.find('[data-testid="account-error"]').text()).toBe("当前密码不正确。");
    expect(useAuthStore().isAuthenticated).toBe(true);
  });

  it("signs out from the panel", async () => {
    const store = await signedIn();
    const wrapper = render();

    await wrapper.find('[data-testid="sign-out"]').trigger("click");
    await flushPromises();

    expect(authApi.logoutAccount).toHaveBeenCalled();
    expect(store.isAuthenticated).toBe(false);
  });
});
