// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

// @vitest-environment node
// Response bodies and headers as the proxy's Node.js runtime sees them.

import { describe, expect, it } from "vitest"
import {
  SITE_SECURITY_HEADERS,
  disallowRobotsResponse,
  routeSiteHost,
  siteCacheControl,
  siteHeaders,
} from "../practice-site"

const PRIMARY = "example.com"
const WWW = "www.example.com"

function request(hostname: string, pathname: string, search = "", method = "GET") {
  return { host: hostname, hostname, pathname, search, protocol: "https:", method }
}

describe("routeSiteHost", () => {
  it("serves the requested path on the primary", () => {
    expect(routeSiteHost(request(PRIMARY, "/about/"), { primaryHost: PRIMARY, portalHost: null, hideFromSearch: false })).toEqual({
      kind: "serve",
      pathname: "/about/",
    })
  })

  it("serves on any working host of a practice with no working primary", () => {
    expect(routeSiteHost(request(WWW, "/"), { primaryHost: null, portalHost: null, hideFromSearch: false })).toEqual({ kind: "serve", pathname: "/" })
  })

  it("sends the www host to the primary with a 301, path and query kept", () => {
    expect(routeSiteHost(request(WWW, "/team/", "?ref=card"), { primaryHost: PRIMARY, portalHost: null, hideFromSearch: false })).toEqual({
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
    expect(routeSiteHost(request(PRIMARY, "/contact", "", method), { primaryHost: PRIMARY, portalHost: null, hideFromSearch: false })).toEqual({
      kind: "method-not-allowed",
    })
  })

  it("serves the frontend's own routes as website paths, never the app", () => {
    for (const path of ["/api/login", "/api/config", "/dashboard", "/__/auth/handler", "/portal/acme"]) {
      expect(routeSiteHost(request(PRIMARY, path), { primaryHost: PRIMARY, portalHost: null, hideFromSearch: false })).toEqual({ kind: "serve", pathname: path })
    }
  })
})

describe("routeSiteHost on a hosted website address", () => {
  const HOSTED = "acme.hosted.example"
  const PORTAL = "acme.portal.hosted.example"

  it("serves the website when the practice has no primary of its own", () => {
    expect(routeSiteHost(request(HOSTED, "/about/"), { primaryHost: null, portalHost: PORTAL, hideFromSearch: false })).toEqual({
      kind: "serve",
      pathname: "/about/",
    })
  })

  it.each([
    ["/portal", "/"],
    ["/portal/", "/"],
    ["/portal/forms", "/forms"],
  ])("sends %s to the portal's host, never serving it on the website's origin", (path, rest) => {
    expect(routeSiteHost(request(HOSTED, path, "?from=card"), { primaryHost: null, portalHost: PORTAL, hideFromSearch: false })).toEqual({
      kind: "redirect",
      location: `https://${PORTAL}${rest}?from=card`,
    })
  })

  it("sends /portal to the portal even when the website itself has moved to the practice's own domain", () => {
    expect(
      routeSiteHost(request(HOSTED, "/portal/forms"), { primaryHost: PRIMARY, portalHost: "portal.example.com", hideFromSearch: false }),
    ).toEqual({ kind: "redirect", location: "https://portal.example.com/forms" })
  })

  it("sends everything else to the practice's own primary once it has one", () => {
    expect(routeSiteHost(request(HOSTED, "/team/", "?ref=card"), { primaryHost: PRIMARY, portalHost: PORTAL, hideFromSearch: false })).toEqual({
      kind: "redirect",
      location: `https://${PRIMARY}/team/?ref=card`,
    })
  })

  it("serves a path that only starts like the portal's", () => {
    expect(routeSiteHost(request(HOSTED, "/portals/"), { primaryHost: null, portalHost: PORTAL, hideFromSearch: false })).toEqual({
      kind: "serve",
      pathname: "/portals/",
    })
  })
})

describe("routeSiteHost for a practice that asks search engines not to list its website", () => {
  const HIDDEN = { primaryHost: PRIMARY, portalHost: null, hideFromSearch: true }

  it.each([PRIMARY, WWW])("answers robots.txt on %s itself, in place of the website's", (host) => {
    expect(routeSiteHost(request(host, "/robots.txt"), HIDDEN)).toEqual({ kind: "disallow-robots" })
    expect(routeSiteHost(request(host, "/robots.txt", "", "HEAD"), HIDDEN)).toEqual({ kind: "disallow-robots" })
  })

  it("serves every other path as before", () => {
    expect(routeSiteHost(request(PRIMARY, "/about/robots.txt"), HIDDEN)).toEqual({ kind: "serve", pathname: "/about/robots.txt" })
    expect(routeSiteHost(request(WWW, "/"), HIDDEN)).toEqual({ kind: "redirect", location: `https://${PRIMARY}/` })
  })

  it("leaves robots.txt to the website when the practice has not asked", () => {
    expect(routeSiteHost(request(PRIMARY, "/robots.txt"), { ...HIDDEN, hideFromSearch: false })).toEqual({
      kind: "serve",
      pathname: "/robots.txt",
    })
  })
})

describe("siteHeaders", () => {
  it("adds noindex, nofollow only when the practice asks", () => {
    expect(siteHeaders(true)).toEqual({ ...SITE_SECURITY_HEADERS, "X-Robots-Tag": "noindex, nofollow" })
    expect(siteHeaders(false)).toEqual(SITE_SECURITY_HEADERS)
    expect(siteHeaders(false)).not.toHaveProperty("X-Robots-Tag")
  })
})

describe("disallowRobotsResponse", () => {
  it("disallows everything, as plain text that says noindex", async () => {
    const response = disallowRobotsResponse("GET")

    expect(response.status).toBe(200)
    expect(await response.text()).toBe("User-agent: *\nDisallow: /\n")
    expect(response.headers.get("content-type")).toBe("text/plain; charset=utf-8")
    expect(response.headers.get("x-robots-tag")).toBe("noindex, nofollow")
    expect(response.headers.get("x-content-type-options")).toBe("nosniff")
    expect(response.headers.get("cache-control")).toBe("public, max-age=3600")
  })

  it("answers HEAD with the headers and no body", async () => {
    const response = disallowRobotsResponse("HEAD")

    expect(response.status).toBe(200)
    expect(await response.text()).toBe("")
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
