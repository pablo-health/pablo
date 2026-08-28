// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"

import { completionPathAfterHandoff } from "../nativeAuthCompletion"

describe("completionPathAfterHandoff", () => {
  it("returns the dashboard path for a custom-scheme redirect", () => {
    expect(completionPathAfterHandoff("pablohealth://callback")).toBe(
      "/dashboard?from=companion",
    )
  })

  it("returns null for a loopback http redirect (navigation leaves the page)", () => {
    expect(completionPathAfterHandoff("http://127.0.0.1:53211/callback")).toBeNull()
  })

  it("returns null for an https redirect", () => {
    expect(completionPathAfterHandoff("https://example.test/cb")).toBeNull()
  })

  it("returns null for an invalid URL", () => {
    expect(completionPathAfterHandoff("not-a-url")).toBeNull()
  })
})
