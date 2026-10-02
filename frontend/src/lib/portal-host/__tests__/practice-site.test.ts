// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { routeSiteHost, siteCacheControl } from "../practice-site"

const PRIMARY = "example.com"
const WWW = "www.example.com"

function request(hostname: string, pathname: string, search = "", method = "GET") {
  return { host: hostname, hostname, pathname, search, protocol: "https:", method }
}

describe("routeSiteHost", () => {
  it("serves the requested path on the primary", () => {
    expect(routeSiteHost(request(PRIMARY, "/about/"), { primaryHost: PRIMARY, portalHost: null })).toEqual({
      kind: "serve",
      pathname: "/about/",
    })
  })

  it("serves on any working host of a practice with no working primary", () => {
    expect(routeSiteHost(request(WWW, "/"), { primaryHost: null, portalHost: null })).toEqual({ kind: "serve", pathname: "/" })
  })

  it("sends the www host to the primary with a 301, path and query kept", () => {
    expect(routeSiteHost(request(WWW, "/team/", "?ref=card"), { primaryHost: PRIMARY, portalHost: null })).toEqual({
      kind: "redirect",
      location: `https://${PRIMARY}/team/?ref=card`,
    })
  })

  it("answers a host that serves no website the same as an unknown one", () => {
    expect(routeSiteHost(request(PRIMARY, "/"), null)).toEqual({ kind: "not-found" })
  })

  it("answers 503 when the lookup could not be made", () => {
    expect(routeSiteHost(request(PRIMARY, "/"), "unavailable")).toEqual({ kind: "unavailable" })
  })

  it.each(["POST", "PUT", "DELETE"])("refuses %s: a website is static", (method) => {
    expect(routeSiteHost(request(PRIMARY, "/contact", "", method), { primaryHost: PRIMARY, portalHost: null })).toEqual({
      kind: "method-not-allowed",
    })
  })

  it("serves the frontend's own routes as website paths, never the app", () => {
    for (const path of ["/api/login", "/api/config", "/dashboard", "/__/auth/handler", "/portal/acme"]) {
      expect(routeSiteHost(request(PRIMARY, path), { primaryHost: PRIMARY, portalHost: null })).toEqual({ kind: "serve", pathname: path })
    }
  })
})

describe("routeSiteHost on a hosted website address", () => {
  const HOSTED = "acme.hosted.example"
  const PORTAL = "acme.portal.hosted.example"

  it("serves the website when the practice has no primary of its own", () => {
    expect(routeSiteHost(request(HOSTED, "/about/"), { primaryHost: null, portalHost: PORTAL })).toEqual({
      kind: "serve",
      pathname: "/about/",
    })
  })

  it.each([
    ["/portal", "/"],
    ["/portal/", "/"],
    ["/portal/forms", "/forms"],
  ])("sends %s to the portal's host, never serving it on the website's origin", (path, rest) => {
    expect(routeSiteHost(request(HOSTED, path, "?from=card"), { primaryHost: null, portalHost: PORTAL })).toEqual({
      kind: "redirect",
      location: `https://${PORTAL}${rest}?from=card`,
    })
  })

  it("sends /portal to the portal even when the website itself has moved to the practice's own domain", () => {
    expect(
      routeSiteHost(request(HOSTED, "/portal/forms"), { primaryHost: PRIMARY, portalHost: "portal.example.com" }),
    ).toEqual({ kind: "redirect", location: "https://portal.example.com/forms" })
  })

  it("sends everything else to the practice's own primary once it has one", () => {
    expect(routeSiteHost(request(HOSTED, "/team/", "?ref=card"), { primaryHost: PRIMARY, portalHost: PORTAL })).toEqual({
      kind: "redirect",
      location: `https://${PRIMARY}/team/?ref=card`,
    })
  })

  it("serves a path that only starts like the portal's", () => {
    expect(routeSiteHost(request(HOSTED, "/portals/"), { primaryHost: null, portalHost: PORTAL })).toEqual({
      kind: "serve",
      pathname: "/portals/",
    })
  })
})

describe("siteCacheControl", () => {
  it("keeps pages a minute and other files an hour", () => {
    expect(siteCacheControl(200, "text/html; charset=utf-8")).toBe("public, max-age=60")
    expect(siteCacheControl(200, "text/css; charset=utf-8")).toBe("public, max-age=3600")
    expect(siteCacheControl(404, "text/html; charset=utf-8")).toBe("public, max-age=60")
    expect(siteCacheControl(404, "text/plain; charset=utf-8")).toBe("public, max-age=60")
  })
})
