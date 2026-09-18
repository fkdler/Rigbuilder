import { defineConfig, devices } from "@playwright/test";

/**
 * Live smoke configuration: runs `e2e/livesmoke.live.ts` against the real local stack
 * (Vite dev server proxying to the running FastAPI backend) with no API mocking.
 *
 * Kept in a separate config so the default `playwright test` run stays hermetic and does
 * not require the backend to be up. This is the check that caught `by_day` being read off
 * the job stats payload instead of the Agent stats payload, a mistake every mocked suite
 * happily agreed with.
 *
 * Usage: npm run test:live
 *        $env:RIGBUILDER_BASE_URL="http://127.0.0.1:5173"; npm run test:live
 *        (the override targets an already-running dev server and skips starting one)
 */
const baseURL = process.env.RIGBUILDER_BASE_URL;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.live.ts",
  timeout: 60_000,
  use: { baseURL: baseURL ?? "http://127.0.0.1:4173" },
  // Only manage a dev server when we are not already pointing at one.
  webServer: baseURL ? undefined : {
    command: "npm run dev -- --host 127.0.0.1 --port 4173",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: true,
  },
  projects: [{ name: "desktop", use: { ...devices["Desktop Chrome"] } }],
});
