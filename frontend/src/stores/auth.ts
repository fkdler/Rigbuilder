import { defineStore } from "pinia";
import { computed, ref } from "vue";

import {
  authErrorMessage, fetchMe, loginAccount, logoutAccount, registerAccount, updatePassword,
  updateUsername, type AuthUser,
} from "@/api/auth";
import {
  AUTH_STORAGE_KEYS, clearIdentityScopedStorage, clearToken, readToken, setSessionInvalidationHandler, writeToken,
} from "@/api/token";
import { useConversationStore } from "@/stores/conversations";

/**
 * The single source of truth for "who is using this browser".
 *
 * Two rules this store exists to enforce (Plan_V4.5 §11.3 U3):
 *
 * 1. the account is only ever established from a server response -- a token that
 *    the server accepts -- never from local storage alone;
 * 2. losing that account wipes everything that belonged to it, so switching users
 *    cannot leave the previous account's conversations on screen, in the Pinia
 *    caches or in the last-opened-session key.
 */
export const useAuthStore = defineStore("auth", () => {
  const user = ref<AuthUser | null>(null);
  const ready = ref(false);
  const pending = ref(false);
  const error = ref<string | null>(null);

  // Modal visibility lives here rather than in a component so the sidebar, the
  // terminal and the app shell can all drive the same overlay without prop drilling.
  const loginModalOpen = ref(false);
  const userModalOpen = ref(false);

  const isAuthenticated = computed(() => user.value !== null);
  const isAdmin = computed(() => user.value?.role === "admin");
  const displayName = computed(() => user.value?.username ?? "");

  function applySession(token: string, nextUser: AuthUser): void {
    if (user.value?.id !== nextUser.id) {
      clearIdentityScopedStorage();
      useConversationStore().resetForSignOut();
    }
    writeToken(token);
    user.value = nextUser;
    error.value = null;
  }

  function resetLocalState(): void {
    loginModalOpen.value = false;
    clearToken();
    clearIdentityScopedStorage();
    user.value = null;
    userModalOpen.value = false;
    // Clearing the conversation store is what makes an account switch safe: its
    // caches, runners and open SSE streams all belong to the previous identity.
    useConversationStore().resetForSignOut();
  }

  // A 401 from any request means the server rejected the token; the axios layer
  // calls this so the UI notices immediately instead of showing stale data.
  setSessionInvalidationHandler(() => {
    user.value = null;
    userModalOpen.value = false;
    useConversationStore().resetForSignOut();
  });

  /** Restore a session from a stored token, before the first render. */
  async function bootstrap(): Promise<void> {
    const token = readToken();
    if (!token) {
      ready.value = true;
      return;
    }
    try {
      const restored = await fetchMe();
      if (readToken() === token) user.value = restored;
    } catch {
      // Expired, revoked, or the server is down: the token is not usable.
      if (readToken() === token) resetLocalState();
    } finally {
      ready.value = true;
    }
  }

  if (typeof window !== "undefined") {
    window.addEventListener("storage", (event) => {
      if (event.key !== AUTH_STORAGE_KEYS.TOKEN_KEY) return;
      user.value = null;
      userModalOpen.value = false;
      useConversationStore().resetForSignOut();
      if (event.newValue) void bootstrap();
    });
  }

  async function signIn(username: string, password: string): Promise<boolean> {
    return run(async () => {
      const session = await loginAccount(username, password);
      applySession(session.token, session.user);
      loginModalOpen.value = false;
    });
  }

  async function signUp(username: string, password: string): Promise<boolean> {
    return run(async () => {
      const session = await registerAccount(username, password);
      applySession(session.token, session.user);
      loginModalOpen.value = false;
    });
  }

  async function signOut(): Promise<void> {
    pending.value = true;
    try {
      if (readToken()) await logoutAccount();
    } catch {
      // A failed revoke must not trap the user in the session locally; the token
      // still expires server-side, and signing out locally is what they asked for.
    } finally {
      pending.value = false;
      resetLocalState();
    }
  }

  async function rename(username: string): Promise<boolean> {
    return run(async () => {
      user.value = await updateUsername(username);
    });
  }

  async function changePassword(currentPassword: string, newPassword: string): Promise<boolean> {
    return run(async () => {
      await updatePassword(currentPassword, newPassword);
    });
  }

  async function run(action: () => Promise<void>): Promise<boolean> {
    pending.value = true;
    error.value = null;
    try {
      await action();
      return true;
    } catch (cause) {
      error.value = authErrorMessage(cause);
      return false;
    } finally {
      pending.value = false;
    }
  }

  function openLoginModal(): void { loginModalOpen.value = true; }
  function closeLoginModal(): void { loginModalOpen.value = false; error.value = null; }
  function openUserModal(): void {
    if (!isAuthenticated.value) { openLoginModal(); return; }
    userModalOpen.value = true;
  }
  function closeUserModal(): void { userModalOpen.value = false; error.value = null; }
  function clearError(): void { error.value = null; }

  return {
    user, ready, pending, error, loginModalOpen, userModalOpen,
    isAuthenticated, isAdmin, displayName,
    bootstrap, signIn, signUp, signOut, rename, changePassword,
    openLoginModal, closeLoginModal, openUserModal, closeUserModal, clearError,
  };
});
