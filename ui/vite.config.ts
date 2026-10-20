/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The built app is served by the Supervisor at /admin; in dev, /v1 and /health are proxied to a local Supervisor.
const supervisor = process.env.SUPERVISOR_URL ?? "http://127.0.0.1:8001";

export default defineConfig({
  base: "/admin/",
  plugins: [react(), tailwindcss()],
  server: { proxy: { "/v1": supervisor, "/health": supervisor } },
  build: { outDir: "dist", sourcemap: false },
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/test/setup.ts"], css: false },
});
