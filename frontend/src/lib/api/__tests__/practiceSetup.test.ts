// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * waitOutPracticeSetup holds a request through a practice-setup 503, asking
 * again on each Retry-After, and gives the last 503 back once the budget would
 * be overrun. apiClient uses it for client-managed requests only.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ApiError, get, setApiUrl } from "../client"
import {
  PRACTICE_SETUP_BUDGET_MS,
  isPracticeSetupInProgress,
  resetPracticeSetupForTests,
  retryAfterMs,
  waitOutPracticeSetup,
} from "../practiceSetup"

vi.mock("@/lib/auth/provider", () => ({
  getClientAuthProvider: () => ({ getIdToken: vi.fn().mockResolvedValue("tok"), signOut: vi.fn() }),
}))
vi.mock("../client.extensions", () => ({ apiErrorInterceptors: [] }))

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  })
}

function setup503(code = "TENANT_PROVISIONING_PENDING", retryAfter = "1"): Response {
  return json(
    503,
    { detail: { error: { code, message: "Your practice is being set up.", details: {} } } },
    { "retry-after": retryAfter },
  )
}

const codeOf = async (r: Response): Promise<string | null> => {
  const body = (await r.clone().json()) as { detail?: { error?: { code?: string } } }
  return body.detail?.error?.code ?? null
}

beforeEach(() => {
  resetPracticeSetupForTests()
})

describe("retryAfterMs", () => {
  it("reads whole seconds and keeps them between one and thirty", () => {
    expect(retryAfterMs(setup503(undefined, "1"))).toBe(1000)
    expect(retryAfterMs(setup503(undefined, "5"))).toBe(5000)
    expect(retryAfterMs(setup503(undefined, "0"))).toBe(1000)
    expect(retryAfterMs(setup503(undefined, "600"))).toBe(30000)
    expect(retryAfterMs(setup503(undefined, "soon"))).toBe(1000)
    expect(retryAfterMs(json(503, {}))).toBe(1000)
  })
})

describe("waitOutPracticeSetup", () => {
  it("hands back anything that is not a setup 503 without asking again", async () => {
    const refetch = vi.fn()
    const other = json(503, { detail: { error: { code: "CLEARINGHOUSE_DOWN" } } })
    expect(await waitOutPracticeSetup(other, { codeOf, refetch })).toBe(other)
    expect(refetch).not.toHaveBeenCalled()
  })

  it("asks again after each Retry-After until the answer is not a setup 503", async () => {
    const slept: number[] = []
    const ok = json(200, {})
    const refetch = vi
      .fn()
      .mockResolvedValueOnce(setup503("TENANT_PROVISIONING_IN_PROGRESS", "5"))
      .mockResolvedValueOnce(ok)
    const seen: boolean[] = []
    const result = await waitOutPracticeSetup(setup503(), {
      codeOf,
      refetch,
      now: () => 0,
      sleep: async (ms) => {
        slept.push(ms)
        seen.push(isPracticeSetupInProgress())
      },
    })
    expect(result).toBe(ok)
    expect(slept).toEqual([1000, 5000])
    expect(seen).toEqual([true, true])
    expect(isPracticeSetupInProgress()).toBe(false)
  })

  it("gives the last 503 back once the next wait would pass the budget", async () => {
    let clock = 0
    const refetch = vi.fn(async () => setup503())
    const result = await waitOutPracticeSetup(setup503(), {
      codeOf,
      refetch,
      now: () => clock,
      sleep: async (ms) => {
        clock += ms
      },
    })
    expect(result.status).toBe(503)
    expect(refetch).toHaveBeenCalledTimes(PRACTICE_SETUP_BUDGET_MS / 1000)
    expect(isPracticeSetupInProgress()).toBe(false)
  })
})

describe("apiClient and a practice being set up", () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    setApiUrl("http://test")
    fetchMock.mockReset()
    vi.stubGlobal("fetch", fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("answers with the request's eventual success", async () => {
    fetchMock.mockResolvedValueOnce(setup503()).mockResolvedValueOnce(json(200, { ok: true }))
    await expect(get<{ ok: boolean }>("/api/x")).resolves.toEqual({ ok: true })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it("leaves a caller that owns its token to handle the 503 itself", async () => {
    fetchMock.mockResolvedValueOnce(setup503())
    const failure = get("/api/x", "own-token")
    await expect(failure).rejects.toBeInstanceOf(ApiError)
    await expect(failure).rejects.toMatchObject({
      code: "TENANT_PROVISIONING_PENDING",
      status: 503,
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
