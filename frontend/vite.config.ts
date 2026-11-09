import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const apiTarget = process.env.VITE_DEV_API_TARGET || "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: true,
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          flow: ["@xyflow/react", "@dagrejs/dagre"],
        },
      },
    },
  },
});
