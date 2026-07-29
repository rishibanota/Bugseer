import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build output is committed into the Python package so `bugseer serve`
// works without a node toolchain. It is deliberately NOT called "dist" so it
// survives tooling that ignores build directories.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../bugseer/webui/static",
    emptyOutDir: true,
    assetsDir: "assets",
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8420",
        changeOrigin: true,
      },
    },
  },
});
