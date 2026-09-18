import { expect, test, type Page } from "@playwright/test";

/**
 * Live smoke: no API mocking, so the dev server's proxy reaches the real backend.
 * This is the check that caught `by_day` being read off the wrong payload while the
 * mocked suites stayed green. Run explicitly: `npm run test:live`.
 *
 * Since Plan_V4.5 §11 every data route requires an account, so these tests sign in
 * through the real login overlay first -- which also exercises the account surface
 * against the real database, not against a mock.
 *
 * The account is fixed and created idempotently (log in, register on refusal), so
 * repeated runs do not accumulate rows. Registration always creates ordinary users.
 * The admin smoke uses separately provisioned credentials from
 * RIGBUILDER_LIVE_ADMIN_USER / RIGBUILDER_LIVE_ADMIN_PASSWORD.
 */

const USERNAME = process.env.RIGBUILDER_LIVE_USER ?? "live-operator";
const PASSWORD = process.env.RIGBUILDER_LIVE_PASSWORD ?? "live-operator-password";

/**
 * Sign in through the overlay, registering the account if it does not exist yet.
 *
 * The distinction is made on the server's own message rather than by trying to
 * create the account first, so an existing account is never disturbed and the flow
 * is the same one a person follows.
 */
async function signInOrRegister(page: Page): Promise<void> {
  // No storage clearing here: each test already gets a fresh browser context, and an
  // init script would run on *every* navigation, wiping the token the moment the
  // console test leaves the conversation page.
  await page.goto("/");

  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible({ timeout: 20_000 });

  await dialog.getByRole("tab", { name: "登录" }).click();
  await dialog.getByTestId("auth-username").fill(USERNAME);
  await dialog.getByTestId("auth-password").fill(PASSWORD);
  await dialog.getByTestId("auth-submit").click();

  const refused = dialog.getByText("用户名或密码不正确。");
  // Waiting (rather than probing once) is what makes this reliable: the error is
  // rendered after a real round trip, so a single `isVisible()` raced ahead of it.
  const loginFailed = await refused
    .waitFor({ state: "visible", timeout: 8_000 })
    .then(() => true)
    .catch(() => false);

  if (loginFailed) {
    await dialog.getByRole("tab", { name: "注册" }).click();
    await dialog.getByTestId("auth-username").fill(USERNAME);
    await dialog.getByTestId("auth-password").fill(PASSWORD);
    await dialog.getByTestId("auth-submit").click();
  }

  await expect(page.getByTestId("account-name")).toHaveText(USERNAME, { timeout: 20_000 });
  await expect(dialog).toHaveCount(0);
}

test("signs in through the overlay and reaches the account panel", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));

  await signInOrRegister(page);

  // The sidebar footer is the entry point to the account panel.
  await page.getByTestId("account-button").click();
  const panel = page.getByRole("dialog");
  await expect(panel).toBeVisible();
  await expect(panel.getByTestId("current-username")).toHaveText(USERNAME);
  await expect(panel.getByTestId("save-username")).toBeVisible();
  await expect(panel.getByTestId("save-password")).toBeVisible();

  // Signing out must return to the login overlay, not to a half-working page.
  await panel.getByTestId("sign-out").click();
  await expect(page.getByRole("dialog")).toBeVisible();
  expect(errors).toEqual([]);
});

test("renders the empty administrator workspace against the live backend", async ({ page }) => {
  const username = process.env.RIGBUILDER_LIVE_ADMIN_USER;
  const password = process.env.RIGBUILDER_LIVE_ADMIN_PASSWORD;
  test.skip(!username || !password, "set separately provisioned administrator credentials");
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByTestId("auth-username").fill(username!);
  await dialog.getByTestId("auth-password").fill(password!);
  await dialog.getByTestId("auth-submit").click();
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByRole("main", { name: "RigBuilder 后台工作区" })).toBeVisible();
  await expect(page.getByLabel("ADMIN", { exact: true })).toBeVisible();
  await expect(page.locator(".sidebar button, .sidebar nav")).toHaveCount(0);
  expect(errors).toEqual([]);
});
