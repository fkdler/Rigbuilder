import { expect, test } from "@playwright/test";
import { randomBytes } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";

test("real single hardware, build, introduction and streaming presentation", async ({ page, request }) => {
  test.setTimeout(240_000);
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const registration = await request.post("/api/auth/register", { data: {
    username: `intent-${randomBytes(6).toString("hex")}`, password: randomBytes(24).toString("hex"),
  } });
  expect(registration.ok()).toBeTruthy();
  const { token } = await registration.json();
  const records: unknown[] = [];
  await mkdir("../.local-logs", { recursive: true });
  try {
    await page.goto("/");
    await page.evaluate(value => localStorage.setItem("rigbuilder.auth_token", value), token);
    await page.reload();
    for (const [index, question] of ["推荐一个特别好的 GPU", "推荐一套普通打游戏的配置", "推荐一套普通打游戏的配置", "介绍一下RTX 5090", "只推荐适合游戏的显卡，不要整机", "给这张显卡配一套主机"].entries()) {
      const response = page.waitForResponse(r => r.url().endsWith("/api/query/jobs") && r.request().method() === "POST");
      await page.getByLabel("向 RigBuilder 提问").fill(question);
      await page.getByRole("button", { name: "RUN" }).click();
      const job = await (await response).json();
      if (index === 0) {
        const progress = page.locator(".work-progress");
        const samples: string[] = [];
        await expect.poll(async () => {
          const value = await progress.textContent().catch(() => "");
          if (value) samples.push(value.trim());
          return new Set(samples.map(s => s.match(/↑ ([\d.]+k?) tokens/)?.[1]).filter(Boolean)).size;
        }, { timeout: 30_000, intervals: [80] }).toBeGreaterThan(1);
        records.push({ samples });
        await page.screenshot({ path: "../.local-logs/intent-spinner-desktop.png", fullPage: true });
      }
      await expect(page.locator(".work-progress")).toHaveCount(0, { timeout: 90_000 });
      const result = await request.get(`/api/query/jobs/${job.id}`, { headers: { Authorization: `Bearer ${token}` } });
      const final = await result.json();
      records.push({ question, final });
      expect(final.status).toBe("completed");
      if (index === 3) {
        expect(final.result.kind).toBe("product_info");
        expect(final.result.verification).toBe("facts_verified");
        const introduction = page.getByRole("region", { name: "产品介绍" });
        await expect(introduction).toContainText("32 GiB");
        await expect(introduction.getByRole("link").first()).toBeVisible();
        await expect(introduction).not.toContainText("推荐理由");
        await introduction.scrollIntoViewIfNeeded();
        await page.screenshot({ path: "../.local-logs/intent-introduction-desktop.png", fullPage: true });
      } else {
        expect(final.result.kind).toBe("fusion");
        expect(final.result.agents.map((a: { status: string }) => a.status)).toEqual(["completed", "completed"]);
        if (index === 0 || index === 4) expect(final.result.presentation.core_build).toBeNull();
        else expect(final.result.presentation.core_build.core.map((c: { role: string }) => c.role).sort()).toEqual(["cpu", "gpu"]);
        const block = page.getByRole("region", { name: "推荐结果" }).last();
        await expect(block).not.toContainText(/存储：|散热：|机箱：|散热器与机箱数据/);
        const names = block.locator(".hardware-name");
        for (const name of await names.all()) await expect(name).toHaveCSS("color", "rgb(217, 119, 87)");
      }
    }
    await page.getByRole("region", { name: "推荐结果" }).last().scrollIntoViewIfNeeded();
    await page.screenshot({ path: "../.local-logs/intent-results-desktop.png", fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("region", { name: "推荐结果" }).last().scrollIntoViewIfNeeded();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    await page.screenshot({ path: "../.local-logs/intent-results-mobile.png", fullPage: true });
    expect(errors).toEqual([]);
  } finally {
    await writeFile("../.local-logs/intent-browser.json", JSON.stringify({ records, errors }, null, 2));
    await request.post("/api/auth/logout", { headers: { Authorization: `Bearer ${token}` } });
  }
});
