// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * pollingOptions: a polling query's requests are sent as background requests,
 * which the API checks against the idle sign-out but does not count as
 * activity. Runs the wrapped queryFn through the real apiClient so the header
 * on the wire is what is asserted.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { skipToken } from "@tanstack/react-query"
import { get, setApiUrl } from "@/lib/api/client"
import { isBackgroundRequest } from "@/lib/api/backgroundRequests"
import { pollingOptions } from "../polling"

vi.mock("@/lib/auth/provider", () => ({
  getClientAuthProvider: () => ({ getIdToken: () => Promise.resolve("tok") }),
}))
vi.mock("@/lib/api/client.extensions", () => ({ apiErrorInterceptors: [] }))

const fetchMock = vi.fn()

function sentHeaders(call = 0): Record<string, string> {
  return fetchMock.mock.calls[call][1].headers as Record<string, string>
}

beforeEach(() => {
  setApiUrl("http://test")
  fetchMock.mockReset().mockImplementation(() =>
    Promise.resolve(
      new Response("{}", { status: 200, headers: { "content-type": "application/json" } }),
    ),
  )
  vi.stubGlobal("fetch", fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("pollingOptions", () => {
  it("sets the interval and keeps the other options", () => {
    const interval = () => 3000
    const options = pollingOptions({ queryKey: ["k"], queryFn: () => 1, staleTime: 5 }, interval)

    expect(options.refetchInterval).toBe(interval)
    expect(options.staleTime).toBe(5)
    expect(options.queryKey).toEqual(["k"])
  })

  it("sends the query's requests as background requests", async () => {
    const options = pollingOptions({ queryKey: ["k"], queryFn: () => get("/api/x") }, 60_000)

    await options.queryFn()

    expect(sentHeaders()["X-Pablo-Background"]).toBe("1")
  })

  it("passes the query function's arguments and result through", async () => {
    const queryFn = vi.fn((context: { signal?: AbortSignal }) => context)
    const options = pollingOptions({ queryKey: ["k"], queryFn }, 1000)
    const context = { signal: new AbortController().signal }

    expect(options.queryFn(context)).toBe(context)
    expect(queryFn).toHaveBeenCalledWith(context)
  })

  it("does not mark a request started after the query function awaits", async () => {
    // The documented constraint: the mark covers only what the query function
    // starts synchronously. This is what each poller's own test guards against.
    const options = pollingOptions(
      {
        queryKey: ["k"],
        queryFn: async () => {
          await Promise.resolve()
          return get("/api/x")
        },
      },
      1000,
    )

    await options.queryFn()

    expect(sentHeaders()["X-Pablo-Background"]).toBeUndefined()
  })

  it("ends the mark when the query function returns or throws", () => {
    pollingOptions({ queryKey: ["k"], queryFn: () => 1 }, 1000).queryFn()
    expect(isBackgroundRequest()).toBe(false)

    const throwing = pollingOptions(
      {
        queryKey: ["k"],
        queryFn: (): number => {
          throw new Error("boom")
        },
      },
      1000,
    )
    expect(() => throwing.queryFn()).toThrow("boom")
    expect(isBackgroundRequest()).toBe(false)
  })

  it("leaves a query with no query function to run alone", () => {
    const options = pollingOptions({ queryKey: ["k"], queryFn: skipToken }, 1000)

    expect(options.queryFn).toBe(skipToken)
    expect(options.refetchInterval).toBe(1000)
  })
})
