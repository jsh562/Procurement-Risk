import { defineConfig } from "@playwright/test";

// Both ports are overridable. Not for CI's benefit — the runner is clean — but
// because a developer's machine often already has something on 8000, and a
// spec suite that cannot run locally is one that only ever runs in the gate.
const API_PORT = process.env.WORKLIST_API_PORT ?? "8000";
const WEB_PORT = process.env.WORKLIST_WEB_PORT ?? "3000";

/**
 * FR-032's presentation contract is asserted here rather than in Vitest: type
 * scale, reading order, and whether the as-of date is reachable without hover
 * are properties of a *rendered* page, and jsdom lays nothing out.
 *
 * Both servers start, and that is deliberate. The worklist page is a server
 * component, so its fetch happens in Node rather than the browser — a
 * `page.route()` interception would never see it, and a stubbed response would
 * make these specs assert layout against data no server produced. Running the
 * real serving boundary against the real schema means what is measured here is
 * the page a coordinator would actually get.
 */
export default defineConfig({
  testDir: "./e2e",
  // Serial, and not as a workaround for flakiness. These specs share one Next
  // server, one serving boundary and one database, and several of them change
  // the page's state — a sort key, an adjustment. Running them in parallel
  // against shared mutable state measures contention between the specs rather
  // than the page, and a failure would say nothing about the code under test.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? "github" : "list",
  use: { baseURL: `http://127.0.0.1:${WEB_PORT}`, trace: "on-first-retry" },
  webServer: [
    {
      // The serving boundary. `WORKLIST_TIMEZONE` is pinned so the states these
      // specs assert are the states the fixture was built for — an unset zone
      // would resolve `today` wherever the runner happens to be (FR-038).
      command: `uv run --directory ../api uvicorn api.main:app --host 127.0.0.1 --port ${API_PORT} --log-level warning`,
      url: `http://127.0.0.1:${API_PORT}/api/v1/worklist`,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      env: {
        DATABASE_URL:
          process.env.DATABASE_URL ??
          "postgresql://procurement:local-development-only@localhost:5434/procurement",
        WORKLIST_TIMEZONE: "UTC",
        // The interface tier's origin. Every client-side re-query is
        // cross-origin, so without this the browser blocks each adjustment
        // while the server-rendered first paint still works — the failure looks
        // like an interface bug and is a deployment one.
        WORKLIST_ALLOWED_ORIGINS: `http://127.0.0.1:${WEB_PORT},http://localhost:${WEB_PORT}`,
        UV_NATIVE_TLS: "1",
      },
    },
    {
      command: `npm run build && npm run start -- --port ${WEB_PORT}`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      env: {
        // Both, because the server component fetches from Node and every
        // adjustment fetches from the browser, and Next only inlines
        // NEXT_PUBLIC_-prefixed variables into the client bundle.
        WORKLIST_API_BASE_URL: `http://127.0.0.1:${API_PORT}`,
        NEXT_PUBLIC_WORKLIST_API_BASE_URL: `http://127.0.0.1:${API_PORT}`,
      },
    },
  ],
});
