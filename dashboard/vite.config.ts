import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Planned recodex dashboard API root segments. The CLI remains the source of
// truth today; these routes define the future local server contract.
const API_SEGMENTS =
  "health|overview|sessions|search|import|watch|skills|improvements|exports|settings";
const API_PROXY_PATTERN = `^/(${API_SEGMENTS})(/|$|\\?)`;
const API_PROXY_TARGET = { target: "http://127.0.0.1:8000", changeOrigin: true };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    proxy: { [API_PROXY_PATTERN]: API_PROXY_TARGET },
  },
  preview: {
    port: 3000,
    proxy: { [API_PROXY_PATTERN]: API_PROXY_TARGET },
  },
});
