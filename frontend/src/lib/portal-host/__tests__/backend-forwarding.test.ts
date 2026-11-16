// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { serverForwardHeaders, visitorForwardHeaders } from "../backend-forwarding"

const SECRET = "s3cret"

function incoming(headers: Record<string, string>): Headers {
  return new Headers(headers)
}

describe("serverForwardHeaders", () => {
  it("carries the secret alone: the call is this server's own", () => {
    expect(serverForwardHeaders({ FRONTEND_FORWARD_SECRET: ` ${SECRET} ` })).toEqual({ "X-Pablo-Frontend-Auth": SECRET })
  })

  it("carries nothing without a secret", () => {
    expect(serverForwardHeaders({})).toEqual({})
    expect(serverForwardHeaders({ FRONTEND_FORWARD_SECRET: " " })).toEqual({})
  })
})

describe("visitorForwardHeaders", () => {
  it("names the visitor the proxy in front saw, read from the right, never the address they claimed", () => {
    const headers = visitorForwardHeaders(
      incoming({ "x-forwarded-for": "6.6.6.6, 198.51.100.7", "user-agent": "Browser/1" }),
      { FRONTEND_FORWARD_SECRET: SECRET },
    )
    expect(headers).toEqual({
      "X-Pablo-Frontend-Auth": SECRET,
      "X-Pablo-Client-IP": "198.51.100.7",
      "X-Pablo-Client-UA": "Browser/1",
    })
  })

  it("follows TRUSTED_PROXY_HOPS", () => {
    const headers = visitorForwardHeaders(incoming({ "x-forwarded-for": "198.51.100.7, 10.0.0.2" }), {
      FRONTEND_FORWARD_SECRET: SECRET,
      TRUSTED_PROXY_HOPS: "2",
    })
    expect(headers["X-Pablo-Client-IP"]).toBe("198.51.100.7")
  })

  it("is this server's own call when the visitor's address is not known", () => {
    expect(visitorForwardHeaders(incoming({}), { FRONTEND_FORWARD_SECRET: SECRET })).toEqual({
      "X-Pablo-Frontend-Auth": SECRET,
    })
  })

  it("carries nothing without a secret", () => {
    expect(visitorForwardHeaders(incoming({ "x-forwarded-for": "198.51.100.7" }), {})).toEqual({})
  })
})
