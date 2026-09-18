/**
 * Where the bearer token lives, and how the session ends.
 *
 * Kept in its own dependency-free module because three layers need it and they would
 * otherwise form a cycle: `http.ts` attaches the header, `queryJobs.ts` appends it to
 * the SSE URL, and the auth store owns its lifetime.
 *
 * `invalidateSession` is the single path for "the server no longer accepts this
 * token" (401 from any request, expiry, revocation, account disabled). It clears the
 * credential *and* notifies, so the UI falls back to the login modal instead of
 * leaving a half-working page behind.
 */

const TOKEN_KEY = "rigbuilder.auth_token";
const CONVERSATION_KEY = "rigbuilder.current_conversation_id";

type SessionListener = () => void;

/**
 * Exactly one subscriber: the auth store. A set of listeners would accumulate one
 * per store instance and there is only ever one session per browser tab, so a single
 * slot is both sufficient and leak-free.
 */
let invalidationListener: SessionListener | null = null;

function readStorage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    // A browser with storage disabled must not break the app.
    return null;
  }
}

export function readToken(): string | null {
  return readStorage()?.getItem(TOKEN_KEY) ?? null;
}

export function writeToken(token: string): void {
  readStorage()?.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  readStorage()?.removeItem(TOKEN_KEY);
}

/**
 * Drop the client state that belongs to one identity.
 *
 * The last opened conversation id is account-scoped and must not survive a sign-out
 * or an account switch, otherwise the next account would try to open a session that
 * is not its own. (It would be refused with 404, but the point is not to try.)
 */
export function clearIdentityScopedStorage(): void {
  readStorage()?.removeItem(CONVERSATION_KEY);
}

export function setSessionInvalidationHandler(listener: SessionListener): void {
  invalidationListener = listener;
}

export function invalidateSession(): void {
  clearToken();
  clearIdentityScopedStorage();
  invalidationListener?.();
}

export const AUTH_STORAGE_KEYS = { TOKEN_KEY, CONVERSATION_KEY } as const;
