// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
// @vitest-environment node

/**
 * The server's backend calls pass the end user's IP and agent, with the shared
 * secret, so audit records name the user's device rather than this server. The
 * IP is read from X-Forwarded-For the way the backend reads it. With no secret
 * configured, nothing extra is sent.
 */
import { afterEach, describe, expect, it, vi } from "vitest"

vi.mock("@/lib/auth/provider", () => ({
  getClientAuthProvider: () => ({ getIdToken: vi.fn(), signOut: vi.fn() }),
}))
vi.mock("../client.extensions", () => ({ apiErrorInterceptors: [] }))

const incoming = new Headers()
vi.mock("next/headers", () => ({ headers: async () => incoming }))

import {
  FORWARD_AUTH_HEADER,
  FORWARDED_CLIENT_IP_HEADER,
  FORWARDED_CLIENT_UA_HEADER,
  buildForwardedClientHeaders,
  clientIpFromForwardedFor,
  registerForwardedClientHeaders,
  trustedProxyHops,
} from "../clientForwarding"
import { forwardedClientHeadersForRequest } from "../clientForwarding.server"
import { getAuthHeader } from "../client"

const UA = "Mozilla/5.0 (Macintosh) Safari/605.1.15"

afterEach(() => {
  vi.unstubAllEnvs()
  registerForwardedClientHeaders(undefined)
  for (const name of [...incoming.keys()]) incoming.delete(name)
})

describe("clientIpFromForwardedFor", () => {
  it("reads the proxy-appended end, not the spoofable leftmost entry", () => {
    expect(clientIpFromForwardedFor("1.2.3.4, 203.0.113.7", 1)).toBe("203.0.113.7")
  })

  it("counts trusted hops from the right", () => {
    expect(clientIpFromForwardedFor("1.2.3.4, 203.0.113.7, 35.0.0.1", 2)).toBe("203.0.113.7")
  })

  it("clamps hops to the entries present", () => {
    expect(clientIpFromForwardedFor("203.0.113.7", 5)).toBe("203.0.113.7")
  })

  it("is null with no header", () => {
    expect(clientIpFromForwardedFor(null, 1)).toBeNull()
    expect(clientIpFromForwardedFor(" , ", 1)).toBeNull()
  })
})

describe("trustedProxyHops", () => {
  it("defaults to 1 and rejects nonsense", () => {
    expect(trustedProxyHops(undefined)).toBe(1)
    expect(trustedProxyHops("0")).toBe(1)
    expect(trustedProxyHops("abc")).toBe(1)
    expect(trustedProxyHops("2")).toBe(2)
  })
})

describe("buildForwardedClientHeaders", () => {
  it("sends the IP, agent and secret", () => {
    expect(
      buildForwardedClientHeaders({
        secret: "s3cret",
        forwardedFor: "1.2.3.4, 203.0.113.7",
        userAgent: UA,
        hops: 1,
      }),
    ).toEqual({
      [FORWARD_AUTH_HEADER]: "s3cret",
      [FORWARDED_CLIENT_IP_HEADER]: "203.0.113.7",
      [FORWARDED_CLIENT_UA_HEADER]: UA,
    })
  })

  it("sends nothing without a secret", () => {
    for (const secret of [undefined, "", "  "]) {
      expect(
        buildForwardedClientHeaders({ secret, forwardedFor: "203.0.113.7", userAgent: UA, hops: 1 }),
      ).toEqual({})
    }
  })

  it("sends nothing when the user's IP is unknown", () => {
    expect(
      buildForwardedClientHeaders({ secret: "s3cret", forwardedFor: null, userAgent: UA, hops: 1 }),
    ).toEqual({})
  })
})

describe("server-side getAuthHeader", () => {
  it("adds the forwarding headers to a call made with a token", async () => {
    vi.stubEnv("FRONTEND_FORWARD_SECRET", "s3cret")
    incoming.set("x-forwarded-for", "1.2.3.4, 203.0.113.7")
    incoming.set("user-agent", UA)
    registerForwardedClientHeaders(forwardedClientHeadersForRequest)

    expect(await getAuthHeader("tok")).toEqual({
      Authorization: "Bearer tok",
      [FORWARD_AUTH_HEADER]: "s3cret",
      [FORWARDED_CLIENT_IP_HEADER]: "203.0.113.7",
      [FORWARDED_CLIENT_UA_HEADER]: UA,
    })
  })

  it("sends nothing extra when the secret is unset", async () => {
    vi.stubEnv("FRONTEND_FORWARD_SECRET", "")
    incoming.set("x-forwarded-for", "203.0.113.7")
    registerForwardedClientHeaders(forwardedClientHeadersForRequest)

    expect(await getAuthHeader("tok")).toEqual({ Authorization: "Bearer tok" })
  })

  it("sends nothing extra without a token", async () => {
    vi.stubEnv("FRONTEND_FORWARD_SECRET", "s3cret")
    incoming.set("x-forwarded-for", "203.0.113.7")
    registerForwardedClientHeaders(forwardedClientHeadersForRequest)

    expect(await getAuthHeader()).toEqual({})
  })

  it("still authenticates when reading the request fails", async () => {
    registerForwardedClientHeaders(async () => {
      throw new Error("no request in scope")
    })

    expect(await getAuthHeader("tok")).toEqual({ Authorization: "Bearer tok" })
  })
})
