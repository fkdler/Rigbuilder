import { fileURLToPath, URL } from "node:url";

import vue from "@vitejs/plugin-vue";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src", import.meta.url)),
      },
    },
    server: {
      port: 5173,
      proxy: {
        "/api": {
          // TODO(CONFIG): Point this at FastAPI when it is not on localhost.
          target: env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8000",
          changeOrigin: true,
        },
        "/health": {
          target: env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8000",
          changeOrigin: true,
        },
      },
    },
    build: {
      // The lazily-loaded admin console bundles echarts, measured at 559 kB raw / 189 kB
      // gzip. Raising the limit above that keeps a genuine regression visible instead of
      // training everyone to ignore a warning on every build. The first-screen bundles are
      // what matter and they are guarded by the bundle test in e2e/admin-dashboard.spec.ts.
      chunkSizeWarningLimit: 600,
    },
    test: {
      environment: "jsdom",
      globals: true,
      restoreMocks: true,
      include: ["src/**/*.spec.ts"],
    },
  };
});
