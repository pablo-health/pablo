// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import {
  type PracticeHostAnswer,
  appHostsFromEnv,
  classifyHost,
  isAppHost,
  practiceHostname,
  routePracticeHost,
  servesOnlyPortal,
} from "../practice-host"

const APP = ["app.example.org", "*.run.example.net"]
const PORTAL = ["portal.example.org"]

describe("appHostsFromEnv", () => {
  it("reads APP_HOSTS like PORTAL_HOSTS", () => {
    expect(appHostsFromEnv({ APP_HOSTS: " App.Example.org , ,*.run.example.net" })).toEqual([
      "app.example.org",
      "*.run.example.net",
    ])
    expect(appHostsFromEnv({})).toEqual([])
  })
})

describe("isAppHost", () => {
  it.each([
    ["app.example.org", true],
    ["APP.example.org:443", true],
    ["svc-123.run.example.net", true],
    ["a.b.run.example.net:8080", true],
    ["run.example.net", false],
    ["evilrun.example.net", false],
    ["portal.example.com", false],
  ])("%s -> %s", (host, expected) => {
    expect(isAppHost(host, APP)).toBe(expected)
  })
})

describe("practiceHostname", () => {
  it.each([
    ["Portal.Example.com", "portal.example.com"],
    ["portal.example.com:443", "portal.example.com"],
    ["portal.example.com.", "portal.example.com"],
  ])("reads %s as %s", (host, expected) => {
    expect(practiceHostname(host)).toBe(expected)
  })

  it.each(["", "intranet", "203.0.113.7", "[2001:db8::1]:443", "under_score.example.com", "a..example.com"])(
    "refuses %s",
    (host) => {
      expect(practiceHostname(host)).toBeNull()
    },
  )
})

describe("classifyHost", () => {
  it("looks nothing up when APP_HOSTS is unset", () => {
    expect(classifyHost("portal.example.com", [], PORTAL)).toEqual({ kind: "canonical" })
  })

  it.each([
    "app.example.org",
    "app.example.org:3000",
    "svc-123.run.example.net",
    "portal.example.org",
    "localhost:3000",
    "127.0.0.1:3000",
    "10.0.0.7",
    "[::1]:3000",
    "frontend:3000",
  ])("leaves this deployment's own host %s as it was", (host) => {
    expect(classifyHost(host, APP, PORTAL)).toEqual({ kind: "canonical" })
  })

  it("leaves a request with no Host header as it was", () => {
    expect(classifyHost(null, APP, PORTAL)).toEqual({ kind: "canonical" })
  })

  it("looks any other DNS name up", () => {
    expect(classifyHost("Portal.Example.com:443", APP, PORTAL)).toEqual({
      kind: "practice",
      hostname: "portal.example.com",
    })
  })

  it("refuses a host no practice could hold", () => {
    expect(classifyHost("under_score.example.com", APP, PORTAL)).toEqual({ kind: "unknown" })
  })
})

describe("servesOnlyPortal", () => {
  it.each([
    ["portal.example.org", APP, true],
    ["portal.example.org:443", [], true],
    ["clients.example-therapy.com", APP, true],
  ])("holds for %s, where the clinician app is not served", (host, appHosts, expected) => {
    expect(servesOnlyPortal(host, appHosts, PORTAL)).toBe(expected)
  })

  it.each([
    ["app.example.org", APP],
    ["localhost:3000", APP],
    ["clients.example-therapy.com", []],
    [null, APP],
  ])("does not hold for %s, which also serves the clinician app", (host, appHosts) => {
    expect(servesOnlyPortal(host, appHosts, PORTAL)).toBe(false)
  })
})

describe("routePracticeHost", () => {
  const PRIMARY = "portal.example.com"
  const ALIAS = "clients.example.com"
  const found: PracticeHostAnswer = { slug: "acme", primaryHost: PRIMARY, theme: null, siteHost: null, header: null }

  function on(host: string, pathAndQuery: string, answer: PracticeHostAnswer = found) {
    const url = new URL(pathAndQuery, `https://${host}`)
    return routePracticeHost(
      { host, hostname: host, pathname: url.pathname, search: url.search, protocol: "https:" },
      answer,
    )
  }

  it.each([
    ["/", "/portal/acme"],
    ["/messaging", "/portal/acme/messaging"],
    ["/recover?from=email", "/portal/acme/recover"],
  ])("serves %s on the primary as the practice's portal, in place", (path, rewritten) => {
    expect(on(PRIMARY, path)).toEqual({ kind: "rewrite", pathname: rewritten })
  })

  it("serves on every active host of a practice with no working primary", () => {
    expect(on(ALIAS, "/", { slug: "acme", primaryHost: null, theme: null, siteHost: null, header: null })).toEqual({
      kind: "rewrite",
      pathname: "/portal/acme",
    })
  })

  it("sends an alias to the same path and query on the primary", () => {
    expect(on(ALIAS, "/messaging?x=1")).toEqual({
      kind: "redirect",
      location: `https://${PRIMARY}/messaging?x=1`,
    })
  })

  it.each([
    ["/portal/acme", "/"],
    ["/portal/acme/", "/"],
    ["/portal/acme/recover?from=email", "/recover?from=email"],
    ["/acme/messaging", "/messaging"],
  ])("moves the practice's own slug form %s to the slug-less path", (path, target) => {
    expect(on(PRIMARY, path)).toEqual({ kind: "redirect", location: `https://${PRIMARY}${target}` })
  })

  it("moves the own slug form on an alias straight to the primary", () => {
    expect(on(ALIAS, "/portal/acme/forms")).toEqual({
      kind: "redirect",
      location: `https://${PRIMARY}/forms`,
    })
  })

  it("keeps a request on its own host on the scheme and port it came in on", () => {
    const decision = routePracticeHost(
      {
        host: "portal.example.com:3000",
        hostname: PRIMARY,
        pathname: "/portal/acme/forms",
        search: "",
        protocol: "http:",
      },
      found,
    )
    expect(decision).toEqual({ kind: "redirect", location: "http://portal.example.com:3000/forms" })
  })

  it.each(["/portal/other", "/portal/other/messaging", "/portal", "/portal/"])(
    "never serves another practice: %s is 404",
    (path) => {
      expect(on(PRIMARY, path)).toEqual({ kind: "not-found" })
      expect(on(ALIAS, path)).toEqual({ kind: "not-found" })
    },
  )

  it.each(["/dashboard", "/login", "/dashboard/patients", "/_private", "/__/auth/handler", "/api/logout", "/api/auth/session"])(
    "never serves the clinician app: %s is 404",
    (path) => {
      expect(on(PRIMARY, path)).toEqual({ kind: "not-found" })
    },
  )

  it.each(["/api/config", "/_next/data/x.json", "/favicon.ico", "/robots.txt"])("passes %s through", (path) => {
    expect(on(PRIMARY, path)).toEqual({ kind: "pass" })
  })

  it("answers 404 for a host that serves nothing, whatever the path", () => {
    for (const path of ["/", "/portal/acme", "/api/config", "/dashboard"]) {
      expect(on(PRIMARY, path, null)).toEqual({ kind: "not-found" })
    }
  })

  it("says so when the lookup could not be made", () => {
    expect(on(PRIMARY, "/", "unavailable")).toEqual({ kind: "unavailable" })
  })
})
