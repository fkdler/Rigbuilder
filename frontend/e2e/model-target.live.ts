import { expect, test } from "@playwright/test";
import { randomBytes } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";

test("product evidence and model-target correction against real services", async ({ page, request }) => {
  test.setTimeout(240_000);
  const registration = await request.post("/api/auth/register", { data: {
    username: `model-${randomBytes(6).toString("hex")}`, password: randomBytes(24).toString("hex"),
  } });
  expect(registration.ok()).toBeTruthy();
  const { token } = await registration.json();
  const records: unknown[] = [], errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await mkdir("../.local-logs", { recursive: true });
  try {
    await page.goto("/");
    await page.evaluate(value => localStorage.setItem("rigbuilder.auth_token", value), token);
    await page.reload();
    const questions = ["介绍一下5090D", "我本地要部署量化模型的话，你给我推荐一个", "推荐一套普通打游戏的配置", "我要的是模型，不是配置"];
    for (const [index, question] of questions.entries()) {
      const created = page.waitForResponse(r => r.url().endsWith("/api/query/jobs") && r.request().method() === "POST");
      await page.getByLabel("向 RigBuilder 提问").fill(question);
      await page.getByRole("button", { name: "RUN" }).click();
      const job = await (await created).json();
      await expect(page.locator(".work-progress")).toHaveCount(0, { timeout: 120_000 });
      const response = await request.get(`/api/query/jobs/${job.id}`, { headers: { Authorization: `Bearer ${token}` } });
      const final = await response.json(); records.push({ question, final });
      expect(final.status).toBe("completed");
      if (index === 0) {
        expect(final.result.kind).toBe("product_info");
        const intro = page.getByRole("region", { name: "产品介绍" }).last();
        await expect(intro).toContainText("32 GiB");
        await expect(intro).toContainText("GDDR7");
        const details = intro.locator('details');
        await expect(details).not.toHaveAttribute('open');
        await expect(intro.locator('dd')).not.toContainText(['待补证据']);
        await intro.getByText('资料核对详情', { exact: true }).click();
        await expect(details).toHaveAttribute('open');
        await expect(details).toContainText('待补证据');
        await intro.getByText('资料核对详情', { exact: true }).click();
        await expect(intro).toContainText("产品名称");
        expect(await intro.getByRole("link").count()).toBeGreaterThan(0);
        await intro.scrollIntoViewIfNeeded();
        await page.screenshot({ path: "../.local-logs/product-5090d-desktop.png", fullPage: true });
        await page.setViewportSize({ width: 390, height: 844 });
        await intro.scrollIntoViewIfNeeded();
        await page.screenshot({ path: "../.local-logs/product-5090d-mobile.png", fullPage: true });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
        await page.setViewportSize({ width: 1280, height: 800 });
      } else {
        expect(final.result.kind).toBe("fusion");
        expect(final.result.agents.map((a: { status: string }) => a.status)).toEqual(["completed", "completed"]);
        const block = page.getByRole("region", { name: "推荐结果" }).last();
        if (index === 2) expect(final.result.presentation.core_build.core).toHaveLength(2);
        else {
          expect(final.result.result.top_k.every((c: { candidate_type: string }) => c.candidate_type === "ai_model")).toBeTruthy();
          expect(final.result.presentation.core_build).toBeNull();
          await expect(block).toContainText("以下是为您推荐的模型");
          await expect(block).not.toContainText("以下是为您推荐的硬件");
          await expect(block).toContainText("Q4_K_M");
          await expect(block).not.toContainText(/尚未完整核验|非本机实测|单方面建议|不保证运行速度/);
          expect(await block.getByRole("link").count()).toBeGreaterThan(0);
        }
        if (index === 3) {
          await block.scrollIntoViewIfNeeded();
          await page.screenshot({ path: "../.local-logs/model-recommendation-desktop.png", fullPage: true });
          await page.setViewportSize({ width: 390, height: 844 });
          await block.evaluate(element => element.scrollIntoView({ block: "start" }));
          await page.screenshot({ path: "../.local-logs/model-recommendation-mobile.png", fullPage: true });
          expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
        }
      }
    }
    expect(errors).toEqual([]);
  } finally {
    await writeFile("../.local-logs/model-target-browser.json", JSON.stringify({ records, errors }, null, 2));
    await request.post("/api/auth/logout", { headers: { Authorization: `Bearer ${token}` } });
  }
});
