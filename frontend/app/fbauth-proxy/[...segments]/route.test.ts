// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { NextRequest } from "next/server"
import { GET } from "./route"

const ORIGINAL_ENV = { ...process.env }
const fetchMock = vi.fn()

beforeEach(() => {
  process.env.FIREBASE_PROJECT_ID = "pablo-test"
  vi.stubGlobal("fetch", fetchMock)
  fetchMock.mockReset()
})

afterEach(() => {
  process.env = { ...ORIGINAL_ENV }
  vi.unstubAllGlobals()
})

function proxyGet(headers: Record<string, string>): Promise<Response> {
  const req = new NextRequest("https://app.example.com/__/auth/handler?x=1", { headers })
  return GET(req, { params: Promise.resolve({ segments: ["auth", "handler"] }) })
}

describe("fbauth proxy", () => {
  it("strips the session cookie and authorization header before fetching upstream", async () => {
    fetchMock.mockResolvedValue(new Response("ok", { status: 200 }))

    await proxyGet({
      cookie: "AuthToken=x",
      authorization: "Bearer y",
      "accept-language": "en",
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [target, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(target).toBe("https://pablo-test.firebaseapp.com/__/auth/handler?x=1")
    const sent = init.headers as Headers
    expect(sent.get("cookie")).toBeNull()
    expect(sent.get("authorization")).toBeNull()
    expect(sent.get("accept-language")).toBe("en")
    expect(sent.get("host")).toBe("pablo-test.firebaseapp.com")
  })

  it("does not pass an upstream set-cookie back to the browser", async () => {
    fetchMock.mockResolvedValue(
      new Response("ok", {
        status: 200,
        headers: { "set-cookie": "helper=1; Path=/", "content-type": "text/html" },
      }),
    )

    const res = await proxyGet({})

    expect(res.status).toBe(200)
    expect(res.headers.get("set-cookie")).toBeNull()
    expect(res.headers.get("content-type")).toBe("text/html")
  })
})
