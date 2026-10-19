import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["src/test/setup.ts"],
    restoreMocks: true,
    // tokens.test.ts reads tokens.css as text through ?raw, which Vitest leaves empty unless it processes CSS.
    css: { include: [/tokens\.css/] },
  },
});
