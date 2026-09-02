// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
// Next's own route-source parser. `next build` runs every `source` through
// this and refuses to start if one fails, so the test below uses it rather
// than `new RegExp`, which accepts syntax (capturing groups) that Next does
// not — that gap let an unbuildable config through CI once already.
import { tryToParsePath } from "next/dist/lib/try-to-parse-path.js"
import nextConfig from "../next.config"

describe("next.config.ts compiler.removeConsole", () => {
  it("strips console.log/warn/debug from production bundles but keeps console.error", () => {
    expect(nextConfig.compiler?.removeConsole).toEqual({ exclude: ["error"] })
  })
})

describe("next.config.ts route sources", () => {
  it("parses every headers/rewrites source the way `next build` does", async () => {
    const rewrites = await nextConfig.rewrites!()
    const sources = [
      ...(await nextConfig.headers!()).map((rule) => rule.source),
      ...(Array.isArray(rewrites)
        ? rewrites
        : [
            ...(rewrites.beforeFiles ?? []),
            ...(rewrites.afterFiles ?? []),
            ...(rewrites.fallback ?? []),
          ]
      ).map((rule) => rule.source),
    ]

    expect(sources.length).toBeGreaterThan(0)
    for (const source of sources) {
      const parsed = tryToParsePath(source)
      expect(parsed.error, `${source} failed to parse`).toBeFalsy()
    }
  })
})

describe("next.config.ts headers()", () => {
  async function headersFor(pathname: string) {
    const rules = await nextConfig.headers!()
    const matching = rules.filter((rule) => new RegExp(`^${rule.source}$`).test(pathname))
    const merged = new Map<string, string>()
    for (const rule of matching) {
      for (const header of rule.headers) {
        merged.set(header.key, header.value)
      }
    }
    return merged
  }

  it("serves /__/auth/action with HSTS and a frame-ancestors 'none' CSP", async () => {
    const headers = await headersFor("/__/auth/action")

    expect(headers.get("Strict-Transport-Security")).toBe(
      "max-age=31536000; includeSubDomains; preload"
    )
    expect(headers.get("Content-Security-Policy")).toBe("frame-ancestors 'none'")
    expect(headers.get("X-Content-Type-Options")).toBe("nosniff")
  })

  it("leaves the Firebase auth helper iframe paths without app headers", async () => {
    const handler = await headersFor("/__/auth/handler")
    const iframe = await headersFor("/__/auth/iframe")

    expect(handler.size).toBe(0)
    expect(iframe.size).toBe(0)
  })
})
