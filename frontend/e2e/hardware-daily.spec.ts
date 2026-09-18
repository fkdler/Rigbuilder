import { expect, test } from "@playwright/test";

import { fulfillAuth, seedSession } from "./session";

const CONVERSATION_ID = "11111111-1111-4111-8111-111111111111";

const DAILY = {
  articles: [{
    title: "下代显卡普遍延期｜显卡日报9月18日", content_id: "7882156355494637607",
    content_text: "日报有用记得三连哦\n一、今日价格分析\n显卡价格有所下跌。\n二、新闻速览\n新品消息。",
    url: "https://zhuanlan.zhihu.com/p/2084021781136986760", comment_count: 7, vote_up_count: 30,
    author_name: "Wallace", author_profile_url: "https://www.zhihu.com/people/mimi-86-49",
    author_badge_text: null, edit_time: "2026-09-18T12:00:00Z", authority_level: "4", thumbnail_url: null, content_is_excerpt: true,
  }],
  cache: { status: "fresh", fetched_at: "2026-09-18T12:10:00Z", expires_at: "2026-09-18T18:10:00Z" },
};

test("hardware daily uses the shared sidebar and history returns to terminal", async ({ page }) => {
  await seedSession(page);
  await page.route((url) => new URL(url).pathname.startsWith("/api/"), async (route) => {
    const url = new URL(route.request().url());
    if (await fulfillAuth(route, url.pathname)) return;
    if (url.pathname === "/api/conversations") {
      await route.fulfill({ json: [{ id: CONVERSATION_ID, title: "现有会话", last_message_summary: "上次的装机问题", updated_at: "2026-09-18T12:00:00Z", latest_job_status: "completed", active_job_id: null, running_job: false, status: "active" }] });
      return;
    }
    if (url.pathname === `/api/conversations/${CONVERSATION_ID}`) {
      await route.fulfill({ json: { id: CONVERSATION_ID, status: "active", messages: [], jobs: [] } });
      return;
    }
    if (url.pathname === "/api/hardware-daily/latest") { await route.fulfill({ json: DAILY }); return; }
    await route.fulfill({ status: 404, json: {} });
  });

  await page.goto("/");
  await expect(page.locator(".module-nav button")).toHaveCount(2);
  await page.locator(".module-nav button").nth(1).click();
  await expect(page).toHaveURL(/module=daily/);
  await expect(page.locator(".daily-logo")).toHaveAttribute("aria-label", "HARDWARE DAILY");
  await expect(page.locator(".issue-entry")).toHaveCount(1);
  await page.locator(".issue-entry").click();
  await expect(page.locator(".daily-section h2").first()).toBeVisible();
  await expect(page.locator(".excerpt-notice")).toContainText("新闻速览为搜索摘要");
  await expect(page.locator("main.daily-page")).toHaveCSS("overflow-y", "auto");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();

  await page.locator(".session-select").click();
  await expect(page).toHaveURL(new RegExp(`module=terminal.*conversation=${CONVERSATION_ID}`));
  await expect(page.locator("main.daily-page")).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});
