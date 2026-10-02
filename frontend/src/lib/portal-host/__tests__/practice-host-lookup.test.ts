// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it, vi } from "vitest"
import { createPracticeHostLookup } from "../practice-host-lookup"

const API = "http://backend.internal:8000"

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })
}

function setup(answer: (url: string) => Response | Promise<Response>, options: { maxEntries?: number } = {}) {
  let now = 1_000_000
  const fetch = vi.fn(async (input: RequestInfo | URL) => answer(String(input)))
  const lookup = createPracticeHostLookup({
    apiUrl: () => `${API}/`,
    fetch: fetch as unknown as typeof globalThis.fetch,
    now: () => now,
    ...options,
  })
  return { lookup, fetch, advance: (ms: number) => (now += ms) }
}

describe("createPracticeHostLookup", () => {
  it("asks the backend for the host and reads the answer", async () => {
    const { lookup, fetch } = setup(() => json(200, { slug: "acme", primary_host: "portal.example.com" }))

    expect(await lookup("portal.example.com")).toEqual({ slug: "acme", primaryHost: "portal.example.com", theme: null, siteHost: null })
    expect(fetch).toHaveBeenCalledWith(`${API}/api/portal/hosts/portal.example.com`, expect.anything())
  })

  it("reads the theme the portal wears there", async () => {
    const theme = { version: 1, colors: { accent: "#24504c", text: null }, fonts: { body: "Inter" }, radius: "md" }
    const { lookup } = setup(() => json(200, { slug: "acme", primary_host: null, theme }))

    expect(await lookup("portal.example.com")).toEqual({
      slug: "acme",
      primaryHost: null,
      theme: { colors: { accent: "#24504c" }, fonts: { body: "Inter" }, radius: "md" },
      siteHost: null,
    })
  })

  it("reads the website the portal links back to, and only a plain hostname", async () => {
    const answers = [
      { site_host: "www.acme-therapy.com", expected: "www.acme-therapy.com" },
      { site_host: "javascript:alert(1)//x.com", expected: null },
      { site_host: 7, expected: null },
    ]
    for (const { site_host, expected } of answers) {
      const { lookup } = setup(() => json(200, { slug: "acme", primary_host: null, site_host }))
      expect((await lookup("portal.example.com")) as { siteHost: string | null }).toMatchObject({
        siteHost: expected,
      })
    }
  })

  it("serves the portal in its own look when the theme makes no sense", async () => {
    const { lookup } = setup(() =>
      json(200, { slug: "acme", primary_host: null, theme: { colors: { accent: "red;}" }, fonts: "x" } }),
    )

    expect(await lookup("portal.example.com")).toEqual({ slug: "acme", primaryHost: null, theme: null, siteHost: null })
  })

  it("keeps a found host for a minute, then asks again", async () => {
    const { lookup, fetch, advance } = setup(() => json(200, { slug: "acme", primary_host: null }))

    await lookup("portal.example.com")
    advance(59_999)
    await lookup("portal.example.com")
    expect(fetch).toHaveBeenCalledTimes(1)

    advance(1)
    await lookup("portal.example.com")
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it("keeps a host that serves nothing for a minute too", async () => {
    const { lookup, fetch, advance } = setup(() => json(404, { detail: "Not found." }))

    expect(await lookup("nobody.example.com")).toBeNull()
    expect(await lookup("nobody.example.com")).toBeNull()
    expect(fetch).toHaveBeenCalledTimes(1)

    advance(60_000)
    await lookup("nobody.example.com")
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it.each([
    ["a server error", () => json(500, {})],
    ["an unexpected body", () => json(200, { slug: 7 })],
    [
      "no answer at all",
      () => {
        throw new TypeError("fetch failed")
      },
    ],
  ])("does not keep %s: the next request asks again", async (_label, answer) => {
    const { lookup, fetch } = setup(answer)

    expect(await lookup("portal.example.com")).toBe("unavailable")
    expect(await lookup("portal.example.com")).toBe("unavailable")
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it("shares one call between requests for the same host", async () => {
    let release: (r: Response) => void = () => {}
    const { lookup, fetch } = setup(() => new Promise<Response>((resolve) => (release = resolve)))

    const first = lookup("portal.example.com")
    const second = lookup("portal.example.com")
    release(json(200, { slug: "acme", primary_host: null }))

    expect(await first).toEqual(await second)
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it("keeps at most its bound, dropping the oldest", async () => {
    const { lookup, fetch } = setup(() => json(404, {}), { maxEntries: 2 })

    for (const host of ["a.example.com", "b.example.com", "c.example.com"]) await lookup(host)
    fetch.mockClear()

    await lookup("b.example.com")
    await lookup("c.example.com")
    expect(fetch).not.toHaveBeenCalled()
    await lookup("a.example.com")
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})
