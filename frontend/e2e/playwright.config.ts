// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Playwright configuration for the end-to-end suite.
 *
 * The stack under test is docker-compose.e2e.yml at the repository root:
 * the production frontend bundle, the API, Postgres, the Firebase Auth
 * emulator and the fake clearinghouse. `webServer` brings it up when it is
 * not already running (so `make e2e-up && make e2e` and a bare
 * `npm run test:e2e` both work) and waits on the frontend's config route.
 *
 * Design: docs/design/e2e-harness.md.
 */

import { defineConfig, devices } from "@playwright/test"
import { fileURLToPath } from "node:url"
import { BASE_URL } from "./fixtures/stack"

const repoRoot = fileURLToPath(new URL("../..", import.meta.url))

export default defineConfig({
  testDir: "./specs",
  outputDir: "./test-results",

  timeout: 60 * 1000,
  expect: { timeout: 10 * 1000 },

  // One worker, specs in order: the fakes are deterministic but the stack's
  // state (the clearinghouse log, the patient list) is shared.
  fullyParallel: false,
  workers: 1,

  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,

  reporter: process.env.CI
    ? [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]]
    : [["list"]],

  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  webServer: {
    command: "docker compose -f docker-compose.e2e.yml up --wait",
    cwd: repoRoot,
    url: `${BASE_URL}/api/config`,
    reuseExistingServer: true,
    timeout: 15 * 60 * 1000,
    stdout: "pipe",
    stderr: "pipe",
  },
})
