// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { defineConfig, devices } from "@playwright/test"
import baseConfig from "./playwright.config"

export default defineConfig(baseConfig, {
  testMatch: "portal-accessibility.spec.ts",
  projects: [
    {
      name: "iphone-webkit",
      use: { ...devices["iPhone 13"] },
    },
  ],
})
