import { expect, test } from "@playwright/test";
import { randomBytes } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";

test("real model SSE updates the single-line token counter", async ({ page, request }) => {
  test.setTimeout(180_000);
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const response = await request.post("/api/auth/register", { data: {
    username: `token-${randomBytes(6).toString("hex")}`, password: randomBytes(24).toString("hex"),
  } });
  expect(response.ok()).toBeTruthy();
  const { token } = await response.json();
  try {
    await page.goto("/");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.evaluate(value => localStorage.setItem("rigbuilder.auth_token", value), token);
    await page.reload();
    const jobResponse = page.waitForResponse(response => response.url().endsWith("/api/query/jobs") && response.request().method() === "POST");
    await page.getByLabel("向 RigBuilder 提问").fill("推荐一套打3A游戏的电脑配置，显卡要50系NV");
    await page.getByRole("button", { name: "RUN" }).click();
    const job = await (await jobResponse).json();
    const progress = page.locator(".work-progress");
    await expect(progress).toContainText(/↑ [\d.]+k? tokens/, { timeout: 90_000 });
    await mkdir("../.local-logs", { recursive: true });
    const samples: string[] = [];
    await expect.poll(async () => {
      const text = await progress.textContent().catch(() => "");
      if (text && /tokens/.test(text)) samples.push(text.trim());
      return new Set(samples.map(text => text.match(/↑ ([\d.]+k?) tokens/)?.[1])).size;
    }, { timeout: 60_000, intervals: [150, 250] }).toBeGreaterThan(1);
    await page.screenshot({ path: "../.local-logs/token-stream-desktop.png", fullPage: true });
    await expect(progress).toHaveCount(0, { timeout: 120_000 });
    const result = await request.get(`/api/query/jobs/${job.id}`, { headers: { Authorization: `Bearer ${token}` } });
    const final = await result.json();
    await writeFile("../.local-logs/token-stream-browser.json", JSON.stringify({ job_id: job.id, samples, errors, final }, null, 2));
    expect(final.status).toBe("completed");
    expect(final.result.kind).toBe("fusion");
    expect(final.result.presentation.core_build.core.some((part: { role: string; name: string }) => part.role === "gpu" && /RTX 50/.test(part.name))).toBeTruthy();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    expect(errors).toEqual([]);
  } finally {
    await request.post("/api/auth/logout", { headers: { Authorization: `Bearer ${token}` } });
  }
});
