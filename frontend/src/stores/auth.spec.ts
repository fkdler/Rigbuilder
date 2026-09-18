import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import * as authApi from "@/api/auth";
import { AUTH_STORAGE_KEYS, invalidateSession, readToken, writeToken } from "@/api/token";
import { useAuthStore } from "./auth";
import { useConversationStore } from "./conversations";

vi.mock("@/api/auth", () => ({
  authErrorMessage: vi.fn((cause: unknown) => (cause instanceof Error ? cause.message : "操作失败，请重试。")),
  registerAccount: vi.fn(),
  loginAccount: vi.fn(),
  logoutAccount: vi.fn(),
  fetchMe: vi.fn(),
  updateUsername: vi.fn(),
  updatePassword: vi.fn(),
}));

const USER = {
  id: "11111111-1111-4111-8111-111111111111",
  username: "Ada",
  role: "admin",
  created_at: "2026-09-16T00:00:00Z",
  last_login_at: null,
};

const SESSION = { token: "token-a", expires_at: "2026-10-01T00:00:00Z", user: USER };

describe("auth store", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    vi.mocked(authApi.loginAccount).mockResolvedValue(SESSION);
    vi.mocked(authApi.registerAccount).mockResolvedValue(SESSION);
    vi.mocked(authApi.logoutAccount).mockResolvedValue(undefined);
    vi.mocked(authApi.fetchMe).mockResolvedValue(USER);
    vi.mocked(authApi.updateUsername).mockResolvedValue({ ...USER, username: "Adalovelace" });
    vi.mocked(authApi.updatePassword).mockResolvedValue(undefined);
  });

  it("starts signed out and only becomes ready after bootstrap", async () => {
    const store = useAuthStore();
    expect(store.isAuthenticated).toBe(false);
    expect(store.ready).toBe(false);

    await store.bootstrap();
    expect(store.ready).toBe(true);
    expect(store.isAuthenticated).toBe(false);
    expect(authApi.fetchMe).not.toHaveBeenCalled();
  });

  it("restores a session from a stored token the server still accepts", async () => {
    writeToken("stored-token");
    const store = useAuthStore();

    await store.bootstrap();
    expect(store.isAuthenticated).toBe(true);
    expect(store.displayName).toBe("Ada");
    expect(store.isAdmin).toBe(true);
  });

  it("discards a stored token the server rejects", async () => {
    writeToken("stale-token");
    vi.mocked(authApi.fetchMe).mockRejectedValue(new Error("401"));
    const store = useAuthStore();

    await store.bootstrap();
    expect(store.isAuthenticated).toBe(false);
    expect(readToken()).toBeNull();
  });

  it("stores the token on sign-in and closes the login modal", async () => {
    const store = useAuthStore();
    store.openLoginModal();

    const ok = await store.signIn("Ada", "pw");
    expect(ok).toBe(true);
    expect(readToken()).toBe("token-a");
    expect(store.displayName).toBe("Ada");
    expect(store.loginModalOpen).toBe(false);
  });

  it("surfaces a failed sign-in without establishing a session", async () => {
    vi.mocked(authApi.loginAccount).mockRejectedValue(new Error("用户名或密码不正确。"));
    const store = useAuthStore();

    const ok = await store.signIn("Ada", "wrong");
    expect(ok).toBe(false);
    expect(store.isAuthenticated).toBe(false);
    expect(store.error).toBe("用户名或密码不正确。");
    expect(readToken()).toBeNull();
  });

  it("clears the previous account's conversations on sign-out", async () => {
    const store = useAuthStore();
    await store.signIn("Ada", "pw");

    const conversations = useConversationStore();
    conversations.conversations = [{ id: "c1", status: "active", title: "A 会话", last_message_summary: "x", updated_at: "2026-09-16T00:00:00Z", running_job: false }];
    localStorage.setItem(AUTH_STORAGE_KEYS.CONVERSATION_KEY, "c1");

    await store.signOut();

    expect(authApi.logoutAccount).toHaveBeenCalled();
    expect(store.isAuthenticated).toBe(false);
    expect(readToken()).toBeNull();
    expect(conversations.conversations).toHaveLength(0);
    expect(localStorage.getItem(AUTH_STORAGE_KEYS.CONVERSATION_KEY)).toBeNull();
  });

  it("signs out locally even when the server cannot revoke the token", async () => {
    vi.mocked(authApi.logoutAccount).mockRejectedValue(new Error("offline"));
    const store = useAuthStore();
    await store.signIn("Ada", "pw");

    await store.signOut();
    expect(store.isAuthenticated).toBe(false);
    expect(readToken()).toBeNull();
  });

  it("treats a 401 from any other request as the end of the session", async () => {
    const store = useAuthStore();
    await store.signIn("Ada", "pw");

    const conversations = useConversationStore();
    conversations.conversations = [{ id: "c1", status: "active", title: "A 会话", last_message_summary: "x", updated_at: "2026-09-16T00:00:00Z", running_job: false }];

    invalidateSession();

    expect(store.isAuthenticated).toBe(false);
    expect(readToken()).toBeNull();
    expect(conversations.conversations).toHaveLength(0);
  });

  it("updates the displayed username after a rename", async () => {
    const store = useAuthStore();
    await store.signIn("Ada", "pw");

    const ok = await store.rename("Adalovelace");
    expect(ok).toBe(true);
    expect(authApi.updateUsername).toHaveBeenCalledWith("Adalovelace");
    expect(store.displayName).toBe("Adalovelace");
  });

  it("reports a rejected password change and keeps the session", async () => {
    vi.mocked(authApi.updatePassword).mockRejectedValue(new Error("当前密码不正确。"));
    const store = useAuthStore();
    await store.signIn("Ada", "pw");

    const ok = await store.changePassword("wrong", "new-pw");
    expect(ok).toBe(false);
    expect(store.error).toBe("当前密码不正确。");
    expect(store.isAuthenticated).toBe(true);
  });
});
