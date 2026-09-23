import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

/** Workspace packages are consumed as TypeScript source (no build step):
 *  Vite transpiles them via these aliases. React is deduped so hooks work
 *  across package boundaries. */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@meshagent/ui": fileURLToPath(new URL("../../packages/ui/src", import.meta.url)),
      "@meshagent/graph": fileURLToPath(new URL("../../packages/graph/src", import.meta.url)),
    },
    dedupe: ["react", "react-dom"],
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_BASE || "http://localhost:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
