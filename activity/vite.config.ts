import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The Activity is served same-origin by FastAPI at /activity in production, so the
// build uses that base path. In dev, Vite proxies /api to the local backend.
export default defineConfig({
  base: "/activity/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/tests/setup.ts"],
    globals: true,
    css: false,
    exclude: ["e2e/**", "node_modules/**"],
  },
});
