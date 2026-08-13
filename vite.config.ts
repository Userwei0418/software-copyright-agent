import { defineConfig } from "vite";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  base: process.env.VITE_BASE_PATH || "/",
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: "127.0.0.1",
    watch: { ignored: ["**/src-tauri/**"] },
  },
  envPrefix: ["VITE_", "TAURI_"],
  build: {
    target: "es2022",
    minify: true,
    sourcemap: true,
    rollupOptions: {
      input: {
        app: resolve(root, "index.html"),
        landing: resolve(root, "landing/index.html"),
        demo: resolve(root, "demo/index.html"),
      },
    },
  },
});
