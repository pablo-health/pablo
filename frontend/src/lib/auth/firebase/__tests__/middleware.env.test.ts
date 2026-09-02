// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

describe("firebase middleware startup validation", () => {
  const originalApiUrl = process.env.API_URL

  beforeEach(() => {
    vi.resetModules()
    process.env.DEV_MODE = "true"
  })

  afterEach(() => {
    process.env.API_URL = originalApiUrl
  })

  it("throws at module load when API_URL is not an https origin", async () => {
    process.env.API_URL = "http://api.example.com"
    await expect(import("../middleware")).rejects.toThrow(/must be an https origin/)
  })
})
