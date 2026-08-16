// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * extraPublicPaths tests
 *
 * The default is the load-bearing case: a deployment that sets nothing must
 * contribute nothing, so the provider allowlists stay byte-identical to what
 * they were before the seam existed. Everything else is about not widening
 * the match by accident — the entries are prefix-matched against the request
 * path, so a stray "" or a relative fragment would make far more public than
 * intended.
 */

import { readFileSync } from "fs"
import { join } from "path"
import { describe, it, expect, afterEach, vi } from "vitest"
import { extraPublicPaths } from "../public-paths"

afterEach(() => {
  vi.unstubAllEnvs()
})

describe("extraPublicPaths", () => {
  it("returns nothing when EXTRA_PUBLIC_PATHS is unset", () => {
    vi.stubEnv("EXTRA_PUBLIC_PATHS", undefined)
    expect(extraPublicPaths()).toEqual([])
  })

  it("returns nothing when EXTRA_PUBLIC_PATHS is empty", () => {
    vi.stubEnv("EXTRA_PUBLIC_PATHS", "")
    expect(extraPublicPaths()).toEqual([])
  })

  it("parses a single path", () => {
    vi.stubEnv("EXTRA_PUBLIC_PATHS", "/guest/confirm")
    expect(extraPublicPaths()).toEqual(["/guest/confirm"])
  })

  it("parses a comma-separated list and trims whitespace", () => {
    vi.stubEnv("EXTRA_PUBLIC_PATHS", "/guest/confirm, /invite ,/status")
    expect(extraPublicPaths()).toEqual(["/guest/confirm", "/invite", "/status"])
  })

  it("drops entries that are not absolute paths", () => {
    vi.stubEnv("EXTRA_PUBLIC_PATHS", "guest,  ,../etc,https://elsewhere.example,/ok")
    expect(extraPublicPaths()).toEqual(["/ok"])
  })

  it("drops a trailing comma rather than yielding a match-everything entry", () => {
    vi.stubEnv("EXTRA_PUBLIC_PATHS", "/ok,")
    expect(extraPublicPaths()).toEqual(["/ok"])
  })
})

// Each provider keeps its own PUBLIC_PATHS list, so a seam wired into one and
// not the other is invisible until the wrong provider is deployed. Assert on
// the file text rather than importing the middlewares, which would pull the
// whole auth chain (and the edge runtime globals) into the test environment.
describe("provider middlewares union the deployment paths in", () => {
  const providerDir = join(__dirname, "..")

  for (const provider of ["firebase", "oidc"]) {
    it(`${provider} spreads extraPublicPaths() into PUBLIC_PATHS`, () => {
      const source = readFileSync(join(providerDir, provider, "middleware.ts"), "utf8")
      expect(source).toContain("...extraPublicPaths()")
    })
  }
})
