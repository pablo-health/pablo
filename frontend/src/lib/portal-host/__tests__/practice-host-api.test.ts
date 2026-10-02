// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

// @vitest-environment node
// The DOM environment's Headers drops `Host` as a forbidden name.

import { describe, expect, it, vi } from "vitest"
import type { PracticeHostAnswer } from "../practice-host"
import { practiceHostApiOrigin } from "../practice-host-api"

const ENV = { APP_HOSTS: "app.example.org", PORTAL_HOSTS: "portal.example.org" }

function request(host: string, headers: Record<string, string> = {}, protocol = "http:") {
  return { headers: new Headers({ host, ...headers }), protocol }
}

function lookupAnswering(answer: PracticeHostAnswer) {
  return vi.fn(async () => answer)
}

describe("practiceHostApiOrigin", () => {
  it("is the request's own origin on a practice's working portal host", async () => {
    const lookup = lookupAnswering({ slug: "acme", primaryHost: "portal.example.com", theme: null, siteHost: null })

    const origin = await practiceHostApiOrigin(
      request("Portal.Example.com", { "x-forwarded-proto": "https" }),
      lookup,
      ENV,
    )

    expect(origin).toBe("https://portal.example.com")
    expect(lookup).toHaveBeenCalledWith("portal.example.com")
  })

  it("keeps the port and, with no balancer to say otherwise, the request's scheme", async () => {
    const origin = await practiceHostApiOrigin(
      request("portal.example.com:3080"),
      lookupAnswering({ slug: "acme", primaryHost: null, theme: null, siteHost: null }),
      ENV,
    )

    expect(origin).toBe("http://portal.example.com:3080")
  })

  it.each([
    ["serves nothing", null],
    ["could not be looked up", "unavailable"],
  ] as const)("is null for a host that %s", async (_label, answer) => {
    expect(await practiceHostApiOrigin(request("portal.example.com"), lookupAnswering(answer), ENV)).toBeNull()
  })

  it.each(["app.example.org", "portal.example.org", "localhost:3000", "127.0.0.1:3000"])(
    "is null on this deployment's own host %s, without asking",
    async (host) => {
      const lookup = lookupAnswering({ slug: "acme", primaryHost: null, theme: null, siteHost: null })

      expect(await practiceHostApiOrigin(request(host), lookup, ENV)).toBeNull()
      expect(lookup).not.toHaveBeenCalled()
    },
  )

  it("is null when APP_HOSTS is unset, without asking", async () => {
    const lookup = lookupAnswering({ slug: "acme", primaryHost: null, theme: null, siteHost: null })

    expect(await practiceHostApiOrigin(request("portal.example.com"), lookup, {})).toBeNull()
    expect(lookup).not.toHaveBeenCalled()
  })
})
