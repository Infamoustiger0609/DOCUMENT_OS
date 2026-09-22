import { defineConfig, devices } from "@playwright/test";

// These tests mock every backend call via page.route() (see e2e/helpers.ts) rather
// than hitting a live backend/Supabase instance — see the "Testing" section in
// CLAUDE.md for why.
//
// Runs against a production build (`npm run build && npm run start`), not
// `npm run dev`. This suite used to run against the dev server, tuning
// expect.timeout and then a workers cap to paper over Next dev mode's
// on-demand-per-route compilation (see CLAUDE.md's Performance notes) — each
// added spec file meant another distinct first-visited route competing for
// the dev server's single-job-at-a-time webpack compiler, and the flakiness
// kept coming back worse as the suite grew. A production build has
// everything pre-compiled, so there's no cold-compile queue to contend with
// at all; the one-time build cost is paid once per run instead of smearing
// unpredictable multi-second waits across whichever tests happen to hit an
// uncompiled route first.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: "list",
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "npm run build && npm run start",
    url: "http://localhost:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
