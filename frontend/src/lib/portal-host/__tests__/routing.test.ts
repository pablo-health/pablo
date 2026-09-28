// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { readdirSync, readFileSync } from "fs"
import { join } from "path"
import { describe, expect, it } from "vitest"

import {
  CLINICIAN_ROUTE_SEGMENTS,
  isPortalHost,
  parsePortalHosts,
  PORTAL_FRONTEND_API_ROUTES,
  portalHostsFromEnv,
  routePortalHost,
  type PortalHostRequest,
} from "../routing"

const PORTAL = "portal.example.org"
const APP = "app.example.org"
const HOSTS = [PORTAL]

function req(host: string | null, path: string, protocol = "https:"): PortalHostRequest {
  const q = path.indexOf("?")
  return {
    host,
    pathname: q === -1 ? path : path.slice(0, q),
    search: q === -1 ? "" : path.slice(q),
    protocol,
  }
}

describe("parsePortalHosts", () => {
  it("is empty when unset or blank", () => {
    expect(parsePortalHosts(undefined)).toEqual([])
    expect(parsePortalHosts("")).toEqual([])
    expect(parsePortalHosts(" , ,")).toEqual([])
  })

  it("trims, lowercases and keeps order", () => {
    expect(parsePortalHosts(" Portal.Example.org ,127.0.0.1:3000")).toEqual([
      "portal.example.org",
      "127.0.0.1:3000",
    ])
  })

  it("reads PORTAL_HOSTS from the environment it is given", () => {
    expect(portalHostsFromEnv({ PORTAL_HOSTS: "a.example,b.example" })).toEqual(["a.example", "b.example"])
    expect(portalHostsFromEnv({})).toEqual([])
  })
})

describe("isPortalHost", () => {
  it("matches case-insensitively", () => {
    expect(isPortalHost("PORTAL.Example.ORG", HOSTS)).toBe(true)
  })

  it("does not match a different host, a suffix or a missing host", () => {
    expect(isPortalHost(APP, HOSTS)).toBe(false)
    expect(isPortalHost("evil-portal.example.org", HOSTS)).toBe(false)
    expect(isPortalHost("portal.example.org.evil", HOSTS)).toBe(false)
    expect(isPortalHost(null, HOSTS)).toBe(false)
    expect(isPortalHost("", HOSTS)).toBe(false)
  })

  it("lets an entry without a port match its name on any port", () => {
    expect(isPortalHost("portal.example.org:443", HOSTS)).toBe(true)
  })

  it("holds an entry with a port to that port", () => {
    const hosts = ["127.0.0.1:3000"]
    expect(isPortalHost("127.0.0.1:3000", hosts)).toBe(true)
    expect(isPortalHost("127.0.0.1:3001", hosts)).toBe(false)
    expect(isPortalHost("127.0.0.1", hosts)).toBe(false)
    expect(isPortalHost("localhost:3000", hosts)).toBe(false)
  })

  it("handles an IPv6 literal", () => {
    expect(isPortalHost("[::1]:3000", ["[::1]:3000"])).toBe(true)
    expect(isPortalHost("[::1]:3000", ["[::1]"])).toBe(true)
    expect(isPortalHost("[::1]:3000", ["[::1]:4000"])).toBe(false)
  })
})

describe("routePortalHost with the feature off", () => {
  it.each([
    [PORTAL, "/acme"],
    [PORTAL, "/dashboard"],
    [APP, "/portal/acme"],
    [APP, "/portal/acme/recover?x=1"],
    [APP, "/dashboard"],
    [null, "/"],
  ])("leaves %s %s alone", (host, path) => {
    expect(routePortalHost(req(host, path), [])).toEqual({ kind: "default" })
  })
})

describe("routePortalHost on a portal host", () => {
  const on = (path: string) => routePortalHost(req(PORTAL, path), HOSTS)

  it("rewrites /{slug} to the portal route", () => {
    expect(on("/acme-therapy")).toEqual({ kind: "rewrite", pathname: "/portal/acme-therapy" })
  })

  it("rewrites /{slug}/{rest} to the portal route", () => {
    expect(on("/acme/recover")).toEqual({ kind: "rewrite", pathname: "/portal/acme/recover" })
    expect(on("/acme/a/b/")).toEqual({ kind: "rewrite", pathname: "/portal/acme/a/b/" })
  })

  it("leaves the query to the caller, which keeps the one it has", () => {
    // The decision carries only the path; the proxy appends the request's own
    // search string, so the query survives untouched.
    expect(on("/acme?utm=x")).toEqual({ kind: "rewrite", pathname: "/portal/acme" })
  })

  it("serves /portal/... as it is", () => {
    expect(on("/portal/acme")).toEqual({ kind: "portal" })
    expect(on("/portal/acme/recover")).toEqual({ kind: "portal" })
    expect(on("/portal")).toEqual({ kind: "portal" })
  })

  it.each([
    "/_next/static/chunks/x.js",
    "/_next/image",
    "/_next/data/build/x.json",
    "/api/config",
    "/favicon.ico",
    "/robots.txt",
    "/icon.png",
    "/acme/logo.svg",
  ])("passes %s through", (path) => {
    expect(on(path)).toEqual({ kind: "pass" })
  })

  it.each([
    "/api",
    "/api/login",
    "/api/logout",
    "/api/auth/session",
    "/api/auth/exchange-setup-token",
    "/api/auth/native/exchange",
    "/api/config/extra",
    "/api/patients",
  ])("answers 404 for the frontend API route %s", (path) => {
    expect(on(path)).toEqual({ kind: "not-found" })
  })

  it("has nothing at the root", () => {
    expect(on("/")).toEqual({ kind: "not-found" })
  })

  it.each([...CLINICIAN_ROUTE_SEGMENTS].map((segment) => `/${segment}`))(
    "answers 404 for the clinician route %s",
    (path) => {
      expect(on(path)).toEqual({ kind: "not-found" })
      expect(on(`${path}/anything`)).toEqual({ kind: "not-found" })
    },
  )

  it("answers 404 for the Firebase auth helper", () => {
    expect(on("/__/auth/handler")).toEqual({ kind: "not-found" })
    expect(on("/__/auth/action")).toEqual({ kind: "not-found" })
    expect(on("/__/firebase/init.json")).toEqual({ kind: "not-found" })
  })

  it("does not read an underscore segment as a slug", () => {
    expect(on("/_private")).toEqual({ kind: "not-found" })
  })

  it("treats a dotted first segment as a file, never a slug", () => {
    // The proxy matcher never sends a dotted path here at all; this keeps the
    // helper agreeing with it.
    expect(on("/.well-known/security")).toEqual({ kind: "pass" })
  })

  it("matches the host however it is cased", () => {
    expect(routePortalHost(req("Portal.Example.Org", "/acme"), HOSTS)).toEqual({
      kind: "rewrite",
      pathname: "/portal/acme",
    })
  })
})

describe("routePortalHost off a portal host", () => {
  const off = (path: string, hosts: string[] = HOSTS, protocol = "https:", host = APP) =>
    routePortalHost(req(host, path, protocol), hosts)

  it("redirects /portal/{rest} to the first portal host over https", () => {
    expect(off("/portal/acme", [PORTAL, "other.example.org"])).toEqual({
      kind: "redirect",
      location: "https://portal.example.org/acme",
    })
  })

  it("keeps the query string", () => {
    expect(off("/portal/acme/recover?a=1&b=two")).toEqual({
      kind: "redirect",
      location: "https://portal.example.org/acme/recover?a=1&b=two",
    })
  })

  it("sends bare /portal to the portal host's root", () => {
    expect(off("/portal")).toEqual({ kind: "redirect", location: "https://portal.example.org/" })
  })

  it("is https to a deployed host even when the request arrived over http", () => {
    expect(off("/portal/acme", HOSTS, "http:")).toEqual({
      kind: "redirect",
      location: "https://portal.example.org/acme",
    })
  })

  it("keeps the request's protocol and the port for a loopback portal host", () => {
    expect(off("/portal/acme?x=1", ["127.0.0.1:3000"], "http:", "localhost:3000")).toEqual({
      kind: "redirect",
      location: "http://127.0.0.1:3000/acme?x=1",
    })
  })

  it.each(["/dashboard", "/", "/login", "/portalish", "/api/portal/practice-slug", "/__/auth/handler"])(
    "leaves %s alone",
    (path) => {
      expect(off(path)).toEqual({ kind: "default" })
    },
  )
})

describe("CLINICIAN_ROUTE_SEGMENTS", () => {
  // Every first path segment the app's route tree serves, route groups
  // flattened. A new top-level route that is missing from the list would be
  // read as a practice slug on a portal host — harmless (the portal route
  // serves it, not the clinician page) but a confusing answer.
  function topLevelSegments(dir: string): string[] {
    return readdirSync(dir, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .flatMap((entry) => {
        const name = entry.name
        if (name.startsWith("(") && name.endsWith(")")) return topLevelSegments(join(dir, name))
        if (name.startsWith("_") || name.startsWith(".") || name.startsWith("[")) return []
        if (name === "__tests__") return []
        return [name]
      })
  }

  it("names every top-level clinician route the app serves", () => {
    const appDir = join(__dirname, "..", "..", "..", "..", "app")
    const served = topLevelSegments(appDir).filter((s) => s !== "portal" && s !== "api")
    expect([...served].sort()).toEqual([...CLINICIAN_ROUTE_SEGMENTS].sort())
  })

  it("is reserved by the backend, so no practice is ever given one as its slug", () => {
    // Two lists in two languages, one here and one where slugs are minted.
    // Read the backend's literal rather than trust a copy of it.
    const source = readFileSync(
      join(__dirname, "..", "..", "..", "..", "..", "backend", "app", "portal", "practice_routes.py"),
      "utf8",
    )
    const block = source.match(/_RESERVED_SLUGS = frozenset\(\s*\{([\s\S]*?)\}\s*\)/)
    expect(block, "backend/app/portal/practice_routes.py defines _RESERVED_SLUGS").toBeTruthy()
    const reserved = new Set([...(block as RegExpMatchArray)[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]))

    for (const name of [...CLINICIAN_ROUTE_SEGMENTS, "portal", "api"]) {
      expect(reserved.has(name), `backend reserves "${name}"`).toBe(true)
    }
  })
})

describe("PORTAL_FRONTEND_API_ROUTES", () => {
  // Every route handler under frontend/app/api, as the URL it serves. Dynamic
  // segments are kept as written ("[...nextauth]") — they only ever need to be
  // denied, and any concrete path under them is denied with them.
  function apiRoutes(dir: string, prefix: string): string[] {
    return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
      if (entry.isDirectory()) {
        if (entry.name === "__tests__") return []
        return apiRoutes(join(dir, entry.name), `${prefix}/${entry.name}`)
      }
      return /^route\.(ts|tsx|js)$/.test(entry.name) ? [prefix] : []
    })
  }

  const appApi = join(__dirname, "..", "..", "..", "..", "app", "api")
  const routes = apiRoutes(appApi, "/api")

  it("finds the frontend's API routes at all", () => {
    expect(routes).toContain("/api/config")
  })

  it.each(routes)("decides %s on a portal host: allowed only if listed", (route) => {
    const decision = routePortalHost(req(PORTAL, route), HOSTS)
    expect(decision).toEqual(PORTAL_FRONTEND_API_ROUTES.has(route) ? { kind: "pass" } : { kind: "not-found" })
  })

  it("lists only routes that exist", () => {
    for (const allowed of PORTAL_FRONTEND_API_ROUTES) expect(routes).toContain(allowed)
  })

  it("is the whole of the allow-list the portal needs today", () => {
    // Deliberately exact: widening what the portal host serves is a decision,
    // so it should show up as a change to this line.
    expect([...PORTAL_FRONTEND_API_ROUTES]).toEqual(["/api/config"])
  })
})
