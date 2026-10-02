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
    expect(routeSiteHost(request(PRIMARY, "/about/"), { primaryHost: PRIMARY })).toEqual({
      kind: "serve",
      pathname: "/about/",
    })
  })

  it("serves on any working host of a practice with no working primary", () => {
    expect(routeSiteHost(request(WWW, "/"), { primaryHost: null })).toEqual({ kind: "serve", pathname: "/" })
  })

  it("sends the www host to the primary with a 301, path and query kept", () => {
    expect(routeSiteHost(request(WWW, "/team/", "?ref=card"), { primaryHost: PRIMARY })).toEqual({
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
    expect(routeSiteHost(request(PRIMARY, "/contact", "", method), { primaryHost: PRIMARY })).toEqual({
      kind: "method-not-allowed",
    })
  })

  it("serves the frontend's own routes as website paths, never the app", () => {
    for (const path of ["/api/login", "/api/config", "/dashboard", "/__/auth/handler", "/portal/acme"]) {
      expect(routeSiteHost(request(PRIMARY, path), { primaryHost: PRIMARY })).toEqual({ kind: "serve", pathname: path })
    }
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
