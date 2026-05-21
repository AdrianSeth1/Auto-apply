import { resolve } from "node:path"

import vue from "@vitejs/plugin-vue"
import { defineConfig } from "vite"

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": resolve(__dirname, "./src"),
    },
  },
  server: {
    fs: {
      allow: [resolve(__dirname), resolve(__dirname, "../docs")],
    },
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/favicon.svg": "http://127.0.0.1:8000",
      "/favicon.ico": "http://127.0.0.1:8000",
      "/logo.svg": "http://127.0.0.1:8000",
    },
  },
  build: {
    outDir: resolve(__dirname, "../src/web/static/spa"),
    emptyOutDir: true,
  },
})
