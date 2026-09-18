import type { Page, Route } from "@playwright/test";

/**
 * The account every mocked browser session runs as.
 *
 * `admin` on purpose: the operator console is only offered to an administrator, and
 * one of these specs navigates to it from the sidebar.
 */
export const E2E_USER = {
  id: "00000000-0000-4000-8000-000000000001",
  username: "e2e-user",
  role: "user",
  created_at: "2026-09-16T00:00:00Z",
  last_login_at: null,
};

export const AUTH_TOKEN_KEY = "rigbuilder.auth_token";

/**
 * Give the page a signed-in session before it loads.
 *
 * Since Plan_V4.5 §11 every data route requires an account, so a spec that only mocks
 * `/api/conversations` would now render the login overlay instead of the product.
 *
 * Registered from inside `mockApi`, i.e. after any test-local `addInitScript` that
 * clears storage, because Playwright runs init scripts in registration order and the
 * token has to be written last.
 */
export async function seedSession(page: Page): Promise<void> {
  await page.addInitScript((key) => {
    localStorage.setItem(key, "e2e-token");
  }, AUTH_TOKEN_KEY);
}

/**
 * Answer the account routes, if this request is one of them.
 *
 * Returns whether it handled the request, so each spec can keep its single
 * `**\/api/**` handler and its own 404 fallback.
 */
export async function fulfillAuth(route: Route, pathname: string): Promise<boolean> {
  if (pathname === "/api/auth/me") {
    await route.fulfill({ json: E2E_USER });
    return true;
  }
  if (pathname === "/api/auth/logout") {
    await route.fulfill({ status: 204, body: "" });
    return true;
  }
  return false;
}
