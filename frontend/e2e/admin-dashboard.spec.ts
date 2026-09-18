import { expect, test, type Page } from "@playwright/test";
import { E2E_USER, seedSession } from "./session";

async function session(page: Page, role: "admin" | "user") {
  await seedSession(page);
  let users = [{ ...E2E_USER, username: "Ada", role: "user", status: "active" }, { ...E2E_USER, id: "admin-id", username: "Operator", role: "admin", status: "active" }];
  let entity = { id: "entity-id", entity_key: "hardware/cpu-test", entity_type: "hardware", canonical_name: "Ryzen Test CPU", lifecycle_status: "active", recommendable: true, release_date: null, updated_at: "2026-09-17T00:00:00Z" };
  let maintenance = { revision: "a".repeat(64), aliases: ["Test CPU"],
    fields: [
      { key: "cpu.cores_total", label: "总核心数", kind: "integer", unit: "", minimum: 1, maximum: 4096, value: "8" as string | null, missing: false },
      { key: "cpu.socket", label: "CPU 接口", kind: "text", unit: "", minimum: 0, maximum: 80, value: null as string | null, missing: true },
    ], prices: [] as Record<string, unknown>[], changes: [] as Record<string, unknown>[] };
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (!path.startsWith("/api/")) return route.continue();
    if (path === "/api/auth/me") return route.fulfill({ json: { ...E2E_USER, role } });
    if (path === "/api/auth/logout") return route.fulfill({ status: 204 });
    if (path === "/api/conversations") return route.fulfill({ json: [] });
    if (path === "/api/admin/usage") return route.fulfill({ json: {
      total_tokens: 125680, calls: 42, unknown_calls: 2, period_tokens: 45680, period_calls: 16, period_unknown_calls: 1,
      first_record_at: "2026-09-01T00:00:00Z", metering_since: "2026-09-17T00:00:00Z", timezone: "Asia/Shanghai",
      start: "2026-09-01", end: "2026-09-17", note: "历史数据仅含已保存的调用记录。",
      timing: { samples: 8, avg_response_seconds: 32.5, avg_execution_seconds: 30, avg_start_wait_seconds: 2.5,
        note: "按请求提交日期统计时间完整的成功咨询任务。",
        by_day: [{ day: "2026-09-01", samples: 8, avg_response_seconds: 32.5, avg_execution_seconds: 30, avg_start_wait_seconds: 2.5 },
          { day: "2026-09-02", samples: 0, avg_response_seconds: null, avg_execution_seconds: null, avg_start_wait_seconds: null }] },
      by_day: Array.from({ length: 17 }, (_, i) => ({ day: `2026-09-${String(i + 1).padStart(2, '0')}`, total_tokens: i * 280, calls: i, unknown_calls: 0 })),
    } });
    if (path === "/api/admin/users") return route.fulfill({ json: { total: users.length, items: users } });
    if (path.startsWith("/api/admin/users/") && route.request().method() === "DELETE") {
      users = users.filter(user => !path.endsWith(user.id));
      return route.fulfill({ status: 204 });
    }
    if (path === "/api/admin/catalog") return route.fulfill({ json: { total: 1, items: [entity] } });
    if (path === "/api/admin/catalog/entity-id/maintenance") {
      if (route.request().method() === "PATCH") {
        const body = route.request().postDataJSON();
        maintenance = { ...maintenance, revision: "b".repeat(64), aliases: body.aliases ?? maintenance.aliases,
          fields: maintenance.fields.map(f => Object.hasOwn(body.values, f.key) ? { ...f, value: body.values[f.key], missing: body.values[f.key] === null } : f),
          changes: [{ id: "change-1", action: "maintain_catalog", created_at: "2026-09-18T00:00:00Z", before: {}, after: body.values }] };
        entity.updated_at = "2026-09-18T00:00:00Z";
      }
      return route.fulfill({ json: maintenance });
    }
    if (path === "/api/admin/catalog/entity-id/quotes") {
      const body = route.request().postDataJSON();
      maintenance = { ...maintenance, revision: "c".repeat(64), prices: [body, ...maintenance.prices] };
      return route.fulfill({ status: 201, json: maintenance });
    }
    if (path === "/api/admin/catalog/entity-id") {
      if (route.request().method() === "PATCH") {
        entity = { ...entity, ...route.request().postDataJSON(), updated_at: "2026-09-17T01:00:00Z" };
        return route.fulfill({ json: entity });
      }
      return route.fulfill({ json: { entity, sections: [{ table: "cpu_spec", total: 1, rows: [{ cores_total: 8, threads: 16, socket: "AM5" }] }] } });
    }
    return route.fulfill({ status: 404, json: {} });
  });
}

test("administrator has a shared-style workspace and sign-out-only account panel", async ({ page }, info) => {
  await session(page, "admin");
  const conversationRequests: string[] = [];
  page.on("request", request => { if (new URL(request.url()).pathname.startsWith("/api/conversations")) conversationRequests.push(request.url()); });
  await page.goto("/");
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByRole("main", { name: "RigBuilder 后台工作区" })).toBeVisible();
  if (info.project.name === "mobile") await expect(page.getByLabel("ADMIN", { exact: true })).toBeHidden();
  else await expect(page.getByLabel("ADMIN", { exact: true })).toBeVisible();
  await expect(page.locator(".sidebar")).toHaveCSS("border-radius", "18px");
  for (const selector of [".sidebar", ".admin-content"]) {
    for (const side of ["top", "right", "bottom", "left"]) {
      await expect(page.locator(selector)).toHaveCSS(`border-${side}-width`, "2px");
    }
    await expect(page.locator(selector)).toHaveCSS("background-clip", "padding-box");
  }
  await expect(page.getByRole("navigation", { name: "后台导航" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "数据看板", exact: true })).toBeVisible();
  await expect(page.getByText("125,680", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "请求耗时", exact: true })).toBeVisible();
  await expect(page.getByRole("definition").filter({ hasText: "32.5 秒" })).toBeVisible();
  await page.getByText("查看逐日耗时", { exact: true }).click();
  await expect(page.getByRole("table", { name: "每日请求耗时" }).getByRole("row").last().getByRole("cell")).toHaveText(["2026-09-02", "0", "—", "—", "—"]);
  await page.getByText("查看逐日耗时", { exact: true }).click();
  await expect(page.locator(".sidebar .session-list")).toHaveCount(0);
  await page.screenshot({ path: "../.local-logs/admin-usage-" + info.project.name + ".png", fullPage: true });
  await page.getByTestId("account-button").click();
  await expect(page.getByRole("dialog", { name: "用户中心" })).toBeVisible();
  await expect(page.getByTestId("username-form")).toHaveCount(0);
  await expect(page.getByTestId("password-form")).toHaveCount(0);
  expect(conversationRequests).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "../.local-logs/admin-" + info.project.name + ".png", fullPage: true });
  await page.getByTestId("sign-out").click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator("textarea")).toBeVisible();
});

test("administrator switches sections, confirms account deletion and saves catalogue edits", async ({ page }, info) => {
  await session(page, "admin");
  await page.goto("/admin");
  await page.getByRole("button", { name: /用户管理/ }).click();
  await expect(page).toHaveURL(/section=users/);
  await expect(page.getByRole("cell", { name: "Ada", exact: true })).toBeVisible();
  await page.screenshot({ path: "../.local-logs/admin-users-" + info.project.name + ".png", fullPage: true });
  await page.getByRole("button", { name: "删除账号 Ada" }).click();
  await expect(page.getByRole("button", { name: "永久删除", exact: true })).toBeDisabled();
  await page.getByLabel("输入用户名以确认").fill("Ada");
  await page.getByRole("button", { name: "永久删除", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("cell", { name: "Ada", exact: true })).toHaveCount(0);
  await expect(page.getByText("已删除账号「Ada」。")).toBeVisible();
  await page.getByRole("button", { name: /数据管理/ }).click();
  await page.getByRole("button", { name: "查看与编辑 Ryzen Test CPU" }).click();
  await page.getByLabel("名称", { exact: true }).fill("Updated CPU");
  await page.getByRole("button", { name: "保存修改" }).click();
  await expect(page.getByText("已保存到数据库。")).toBeVisible();
  await page.getByText("CPU 规格 · 1 条", { exact: true }).click();
  await expect(page.getByRole("cell", { name: "AM5", exact: true })).toBeVisible();
  await page.screenshot({ path: "../.local-logs/admin-catalog-" + info.project.name + ".png", fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: "返回列表" }).click();
  await expect(page.getByRole("cell", { name: "Updated CPU hardware/cpu-test", exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "数据管理", exact: true })).toBeVisible();
});

test("usage failures remain errors and can be retried", async ({ page }) => {
  await session(page, "admin");
  let failed = true;
  await page.route("**/api/admin/usage?*", route => failed ? route.fulfill({ status: 503, json: { detail: "统计服务暂不可用" } }) : route.fallback());
  await page.goto("/admin");
  await expect(page.getByRole("alert")).toHaveText("统计服务暂不可用");
  await expect(page.getByText("全站累计 Token · 已记录")).toHaveCount(0);
  failed = false;
  await page.getByRole("button", { name: "查询用量" }).click();
  await expect(page.getByText("125,680", { exact: true })).toBeVisible();
});

test("an outdated backend explains missing administrator routes", async ({ page }) => {
  await session(page, "admin");
  await page.route("**/api/admin/usage?*", route => route.fulfill({ status: 404, json: { detail: "Not Found" } }));
  await page.goto("/admin");
  await expect(page.getByRole("alert")).toContainText("当前后端尚未加载管理员接口");
  await expect(page.getByText("全站累计 Token · 已记录")).toHaveCount(0);
});

test("curated maintenance previews facts, requires sources and appends quotes", async ({ page }, info) => {
  await session(page, "admin");
  await page.goto("/admin?section=catalog");
  await page.getByRole("button", { name: "查看与编辑 Ryzen Test CPU" }).click();
  const maintenance = page.getByRole("region", { name: "关键数据维护" });
  await maintenance.getByLabel("只看待补充项").check();
  await expect(maintenance.getByLabel("总核心数", { exact: true })).toHaveCount(0);
  await maintenance.getByLabel("CPU 接口", { exact: true }).fill("AM5");
  await expect(maintenance.getByText("本次变更", { exact: true })).toBeVisible();
  await expect(page.getByLabel("名称", { exact: true })).toBeDisabled();
  page.once("dialog", d => d.dismiss());
  await page.getByRole("button", { name: "返回列表" }).click();
  await expect(maintenance.getByLabel("CPU 接口", { exact: true })).toHaveValue("AM5");
  const fillSource = async () => {
    await maintenance.getByLabel("来源标题", { exact: true }).fill("厂商规格与报价");
    await maintenance.getByLabel("来源链接", { exact: true }).fill("https://example.com/spec");
    await maintenance.getByLabel("相关原文摘录", { exact: true }).fill("Socket AM5; retail price CNY 1999.");
    await maintenance.getByLabel("修改原因", { exact: true }).fill("补充经核对的缺失信息");
    await maintenance.getByRole("checkbox", { name: /我已核对来源/ }).check();
  };
  await fillSource();
  const request = page.waitForRequest(r => r.method() === "PATCH" && r.url().includes("/maintenance"));
  await maintenance.getByRole("button", { name: "保存关键数据" }).click();
  expect((await request).postDataJSON().values).toEqual({ "cpu.socket": "AM5" });
  await expect(maintenance.getByText("规格与别名已保存，修改记录已更新。")).toBeVisible();
  await maintenance.getByRole("button", { name: "新增报价", exact: true }).click();
  await maintenance.getByLabel("报价金额", { exact: true }).fill("1999");
  await maintenance.getByLabel("采集时间（北京时间）", { exact: true }).fill("2026-09-17T12:00");
  await fillSource();
  await maintenance.getByRole("button", { name: "追加报价", exact: true }).click();
  await expect(maintenance.getByRole("cell", { name: "1999 CNY", exact: true })).toBeVisible();
  await maintenance.getByRole("heading", { name: "关键数据维护" }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: "../.local-logs/catalog-maintenance-" + info.project.name + ".png", fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("maintenance conflict preserves the facts and source draft", async ({ page }) => {
  await session(page, "admin");
  await page.route("**/api/admin/catalog/entity-id/maintenance", route => route.request().method() === "PATCH" ? route.fulfill({ status: 409, json: { detail: "数据已发生变化，请重新读取后核对修改。" } }) : route.fallback());
  await page.goto("/admin?section=catalog");
  await page.getByRole("button", { name: "查看与编辑 Ryzen Test CPU" }).click();
  const maintenance = page.getByRole("region", { name: "关键数据维护" });
  await maintenance.getByLabel(/别名（每行一个/).fill("Changed Alias");
  await maintenance.getByRole("button", { name: "保存关键数据" }).click();
  await expect(maintenance.getByRole("alert")).toContainText("数据已发生变化");
  await expect(maintenance.getByLabel(/别名（每行一个/)).toHaveValue("Changed Alias");
});

test("catalogue conflict retains draft and leaving requires confirmation", async ({ page }) => {
  await session(page, "admin");
  await page.route("**/api/admin/catalog/entity-id", route => route.request().method() === "PATCH" ? route.fulfill({ status: 409, json: { detail: "记录已被其他操作修改，请重新打开详情后再编辑。" } }) : route.fallback());
  await page.goto("/admin?section=catalog");
  await page.getByRole("button", { name: "查看与编辑 Ryzen Test CPU" }).click();
  await page.getByLabel("名称", { exact: true }).fill("Unsaved CPU");
  await page.getByRole("button", { name: "保存修改" }).click();
  await expect(page.getByRole("alert")).toContainText("记录已被其他操作修改");
  await expect(page.getByLabel("名称", { exact: true })).toHaveValue("Unsaved CPU");
  page.once("dialog", dialog => dialog.dismiss());
  await page.getByRole("button", { name: /用户管理/ }).click();
  await expect(page.getByLabel("名称", { exact: true })).toHaveValue("Unsaved CPU");
});

test("ordinary user cannot open the backend route", async ({ page }) => {
  await session(page, "user");
  await page.goto("/admin");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("main", { name: "RigBuilder 后台工作区" })).toHaveCount(0);
});

test("anonymous backend access returns to the homepage without an overlay", async ({ page }) => {
  await page.goto("/admin");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("anonymous visitors open login on demand and keep their draft", async ({ page }, info) => {
  const requests: string[] = [];
  page.on("request", request => { if (new URL(request.url()).pathname.startsWith("/api/")) requests.push(request.url()); });
  await page.goto("/");
  await expect(page.locator("textarea")).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByTestId("account-button").click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await page.locator("textarea").fill("推荐一套游戏配置");
  await page.getByRole("button", { name: "RUN" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.screenshot({ path: "../.local-logs/login-" + info.project.name + ".png", fullPage: true });
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator("textarea")).toHaveValue("推荐一套游戏配置");
  await page.locator("textarea").press("Control+Enter");
  await expect(page.getByRole("dialog")).toBeVisible();
  expect(requests).toEqual([]);
});
