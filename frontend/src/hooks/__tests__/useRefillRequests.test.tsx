// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A decision re-reads both queues — on success, and on the 409 that means a
 * colleague answered first, so the request moves to where it now belongs
 * instead of sitting on screen as if it were still waiting. Any other
 * failure leaves the cache alone.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ApiError } from "@/lib/api/client"
import { queryKeys } from "@/lib/api/queryKeys"
import * as refillApi from "@/lib/api/refillRequests"
import { isAlreadyAnswered, useDecideRefill } from "../useRefillRequests"

vi.mock("@/lib/api/refillRequests")

function setup() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const invalidate = vi.spyOn(queryClient, "invalidateQueries")
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "TestQueryClientWrapper"
  const { result } = renderHook(() => useDecideRefill(), { wrapper: Wrapper })
  return { result, invalidate }
}

const input = { requestId: "req-1", status: "approved" as const, prescriberNote: null }

describe("useDecideRefill", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("posts the decision and re-reads the queues", async () => {
    vi.mocked(refillApi.decideRefillRequest).mockResolvedValue(
      {} as refillApi.ClinicianRefillRequest, // shape is irrelevant to the cache behaviour under test
    )
    const { result, invalidate } = setup()

    result.current.mutate(input)

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(refillApi.decideRefillRequest).toHaveBeenCalledWith(input, undefined)
    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.refills.all })
  })

  it("re-reads the queues when a colleague answered first", async () => {
    vi.mocked(refillApi.decideRefillRequest).mockRejectedValue(
      new ApiError("CONFLICT", "already answered", undefined, 409),
    )
    const { result, invalidate } = setup()

    result.current.mutate(input)

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(isAlreadyAnswered(result.current.error)).toBe(true)
    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.refills.all })
  })

  it("leaves the cache alone on any other failure", async () => {
    vi.mocked(refillApi.decideRefillRequest).mockRejectedValue(
      new ApiError("SERVER_ERROR", "boom", undefined, 500),
    )
    const { result, invalidate } = setup()

    result.current.mutate(input)

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(isAlreadyAnswered(result.current.error)).toBe(false)
    expect(invalidate).not.toHaveBeenCalled()
  })
})
