import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tsconfigPaths from "vite-tsconfig-paths";

export default defineConfig({
  plugins: [react(), tsconfigPaths()],
  test: {
    environment: "jsdom",
    include: ["__tests__/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      reportsDirectory: "coverage",
      // FR-040. Scoped to the worklist rather than the whole boundary: the
      // Next.js starter files still present here would dilute the denominator
      // until a later epic replaces them, and a floor that passes because of
      // untouched scaffolding measures nothing. Fails independently of the
      // Python floor, so neither can mask the other.
      include: ["app/worklist/**/*.{ts,tsx}"],
      thresholds: { lines: 80, branches: 80, functions: 80, statements: 80 },
    },
  },
});
