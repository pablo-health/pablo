// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

// @vitest-environment node
// Response bodies and headers as the proxy's Node.js runtime sees them.

import { describe, expect, it, vi } from "vitest"
import { createHostLookup } from "../practice-host-lookup"
import { fetchSiteFile, parseSiteHost } from "../practice-site-lookup"

const API = "http://backend.internal:8000"

function upstream(status: number, body: string | null, headers: Record<string, string> = {}): Response {
  return new Response(body, { status, headers })
}

function setup(answer: Response | Error) {
  const fetch = vi.fn(async () => {
    if (answer instanceof Error) throw answer
    return answer
  })
  const ask = (pathname: string, extra: { method?: string; ifNoneMatch?: string | null } = {}) =>
    fetchSiteFile(
      {
        hostname: "example.com",
        address: `https://example.com${pathname}`,
        pathname,
        method: extra.method ?? "GET",
        ifNoneMatch: extra.ifNoneMatch ?? null,
      },
      { apiUrl: () => `${API}/`, fetch: fetch as unknown as typeof globalThis.fetch },
    )
  return { fetch, ask }
}

describe("fetchSiteFile", () => {
  it("asks the backend for the path as sent, encoded whole, so nothing resolves it on the way", async () => {
    const { fetch, ask } = setup(upstream(404, "Not Found"))

    await ask("/a/../%2e%2e/b c.html")

    expect(fetch).toHaveBeenCalledWith(
      `${API}/api/sites/hosts/example.com/file?path=${encodeURIComponent("/a/../%2e%2e/b c.html")}`,
      expect.objectContaining({ redirect: "manual" }),
    )
  })

  it("answers with the file, its type, and the website's own headers — never the backend's", async () => {
    const { ask } = setup(
      upstream(200, "<h1>Home</h1>", {
        "Content-Type": "text/html; charset=utf-8",
        ETag: '"v3"',
        // What the backend sends, so the page is inert on the app's origin.
        "Content-Security-Policy": "sandbox; frame-ancestors 'none'",
        "X-Robots-Tag": "noindex",
        "Cache-Control": "no-store",
        "Set-Cookie": "session=leaked",
      }),
    )

    const response = await ask("/")

    expect(response.status).toBe(200)
    expect(await response.text()).toBe("<h1>Home</h1>")
    expect(response.headers.get("content-type")).toBe("text/html; charset=utf-8")
    expect(response.headers.get("etag")).toBe('"v3"')
    expect(response.headers.get("cache-control")).toBe("public, max-age=60")
    expect(response.headers.get("content-security-policy")).toBe(
      "base-uri 'self'; object-src 'none'; frame-ancestors 'none'",
    )
    expect(response.headers.get("x-content-type-options")).toBe("nosniff")
    expect(response.headers.get("referrer-policy")).toBe("strict-origin-when-cross-origin")
    expect(response.headers.get("set-cookie")).toBeNull()
  })

  it("never passes the backend's sandbox on, so the website's own scripts run on its own host", async () => {
    const { ask } = setup(
      upstream(200, "<script>1</script>", {
        "Content-Type": "text/html; charset=utf-8",
        "Content-Security-Policy": "sandbox; frame-ancestors 'none'",
        "X-Robots-Tag": "noindex",
      }),
    )

    const response = await ask("/")

    expect(response.headers.get("content-security-policy")).not.toContain("sandbox")
    expect(response.headers.get("x-robots-tag")).toBeNull()
  })

  it("passes the website's 404 page on with its status", async () => {
    const { ask } = setup(upstream(404, "<h1>Lost</h1>", { "Content-Type": "text/html; charset=utf-8" }))

    const response = await ask("/nope")

    expect(response.status).toBe(404)
    expect(await response.text()).toBe("<h1>Lost</h1>")
  })

  it("sends a folder asked for without its slash to the slashed form", async () => {
    const { ask } = setup(upstream(301, null, { Location: "about/" }))

    const response = await ask("/about")

    expect(response.status).toBe(301)
    expect(response.headers.get("location")).toBe("https://example.com/about/")
  })

  it("forwards what the browser already holds and answers 304 without a body", async () => {
    const { fetch, ask } = setup(upstream(304, null, { ETag: '"v3"' }))

    const response = await ask("/", { ifNoneMatch: '"v3"' })

    expect(response.status).toBe(304)
    expect(fetch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ headers: { "If-None-Match": '"v3"' } }),
    )
  })

  it("answers HEAD with the headers and no body", async () => {
    const { ask } = setup(upstream(200, "body{}", { "Content-Type": "text/css; charset=utf-8" }))

    const response = await ask("/site.css", { method: "HEAD" })

    expect(response.status).toBe(200)
    expect(response.headers.get("cache-control")).toBe("public, max-age=3600")
    expect(await response.text()).toBe("")
  })

  it.each([new Error("unreachable"), upstream(500, "boom")])("answers 503 when the backend fails (%s)", async (answer) => {
    const { ask } = setup(answer)

    const response = await ask("/")

    expect(response.status).toBe(503)
    expect(response.headers.get("retry-after")).toBe("5")
  })
})

describe("parseSiteHost", () => {
  it("reads the primary and, on a hosted address, the portal's host", () => {
    expect(parseSiteHost({ primary_host: null, portal_host: "acme.portal.hosted.example" })).toEqual({
      primaryHost: null,
      portalHost: "acme.portal.hosted.example",
    })
  })

  it("reads an answer with no portal host as one without", () => {
    expect(parseSiteHost({ primary_host: "example.com" })).toEqual({ primaryHost: "example.com", portalHost: null })
  })

  it("makes nothing of a portal host that is not a name", () => {
    expect(parseSiteHost({ primary_host: null, portal_host: 7 })).toBe("unavailable")
  })
})

describe("createHostLookup", () => {
  it("asks the route's own path and parses with the route's own reader", async () => {
    const fetch = vi.fn(async () => upstream(200, JSON.stringify({ primary_host: "example.com" })))
    const lookup = createHostLookup(
      { apiUrl: () => API, fetch: fetch as unknown as typeof globalThis.fetch },
      { path: (host) => `/api/sites/hosts/${host}`, parse: (body) => body as { primary_host: string } },
    )

    expect(await lookup("www.example.com")).toEqual({ primary_host: "example.com" })
    expect(fetch).toHaveBeenCalledWith(`${API}/api/sites/hosts/www.example.com`, expect.anything())
  })
})
