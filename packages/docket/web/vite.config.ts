/**
 * The build writes the app into the Python package (DR-0705), under fixed names: the service serves them no-cache
 * with an ETag, so they need no content hash, and a rebuild changes only what changed. The dev server is for
 * development only (DR-0703): on localhost, never 127.0.0.1, whose session cookie is the owner's, proxying to a
 * throwaway service named by DOCKET_DEV_TARGET, such as one `uv run devkit ui design --keep` started.
 */
import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { docketBuild } from "./scripts/build.js";
import { inputsHash } from "./scripts/inputs.js";

const root = import.meta.dirname;
const target = process.env.DOCKET_DEV_TARGET ?? "http://127.0.0.1:8742";
const proxied = { target, changeOrigin: true, headers: { Origin: target } };

export default defineConfig(({ command }) => ({
  base: "/static/app/",
  // The build's stamp, as build.json holds it, so a page loaded later can tell the service now serves another build.
  define: { __DOCKET_BUILD__: JSON.stringify(command === "build" ? inputsHash(root) : "") },
  plugins: [react(), tailwindcss(), docketBuild({ root })],
  resolve: { alias: { "@": path.resolve(root, "src") } },
  build: {
    outDir: path.resolve(root, "../src/docket/web/app"),
    emptyOutDir: true,
    // No inline script anywhere (DR-0706): the polyfill would be one.
    modulePreload: { polyfill: false },
    cssCodeSplit: false,
    // Fonts as files, never data: URIs.
    assetsInlineLimit: 0,
    reportCompressedSize: false,
    sourcemap: false,
    rolldownOptions: {
      output: {
        entryFileNames: "app.js",
        chunkFileNames: "[name].js",
        assetFileNames: (asset) => (asset.names.some((n) => n.endsWith(".css")) ? "app.css" : "[name][extname]"),
      },
    },
  },
  server: {
    host: "localhost",
    port: 5173,
    strictPort: true,
    proxy: { "/api": proxied, "/session": proxied, "/static/icon.svg": proxied },
  },
}));
