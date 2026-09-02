// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import nextConfig from "../next.config"

describe("next.config.ts compiler.removeConsole", () => {
  it("strips console.log/warn/debug from production bundles but keeps console.error", () => {
    expect(nextConfig.compiler?.removeConsole).toEqual({ exclude: ["error"] })
  })
})
