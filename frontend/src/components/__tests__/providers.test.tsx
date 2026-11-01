// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The app's QueryClient wires a MutationCache-level onError so a mutation
 * that defines no onError of its own still surfaces its failure, instead
 * of failing silently.
 */
import { describe, it, expect, vi } from "vitest"
import { QueryClientProvider, useMutation, useQuery } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import { createAppQueryClient, shouldRetryQuery } from "../providers"
import { ApiError } from "@/lib/api/client"

function apiError(status: number): ApiError {
  return new ApiError("CODE", "message", undefined, status)
}

describe("shouldRetryQuery", () => {
  it.each([400, 401, 403, 404, 422])("does not retry a %i", (status) => {
    expect(shouldRetryQuery(0, apiError(status))).toBe(false)
  })

  it.each([408, 429, 500, 503])("retries a %i", (status) => {
    expect(shouldRetryQuery(0, apiError(status))).toBe(true)
  })

  it("retries a network failure", () => {
    expect(shouldRetryQuery(0, new TypeError("Failed to fetch"))).toBe(true)
  })

  it("stops after three retries", () => {
    expect(shouldRetryQuery(2, apiError(500))).toBe(true)
    expect(shouldRetryQuery(3, apiError(500))).toBe(false)
  })
})

describe("app query defaults", () => {
  // A 403 used to retry three times with backoff, holding the dashboard
  // panels in their loading state for seconds before any error showed.
  it("surfaces a 403 after a single request", async () => {
    const queryClient = createAppQueryClient(vi.fn())
    const queryFn = vi.fn().mockRejectedValue(apiError(403))

    const { result } = renderHook(
      () => useQuery({ queryKey: ["x"], queryFn }),
      { wrapper: wrapperFor(queryClient) },
    )

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(queryFn).toHaveBeenCalledTimes(1)
  })
})

function wrapperFor(queryClient: ReturnType<typeof createAppQueryClient>) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
}

describe("createAppQueryClient", () => {
  it("surfaces a rejected mutation that has no onError of its own", async () => {
    const showToast = vi.fn()
    const queryClient = createAppQueryClient(showToast)

    const { result } = renderHook(
      () =>
        useMutation({
          mutationFn: () => Promise.reject(new Error("Could not save appointment")),
        }),
      { wrapper: wrapperFor(queryClient) }
    )

    result.current.mutate()

    await waitFor(() => expect(showToast).toHaveBeenCalledTimes(1))
    expect(showToast).toHaveBeenCalledWith("Could not save appointment")
  })

  it("never forwards the mutation's request payload, only the error text", async () => {
    const showToast = vi.fn()
    const queryClient = createAppQueryClient(showToast)

    const { result } = renderHook(
      () =>
        useMutation({
          mutationFn: (variables: { patientName: string }) =>
            Promise.reject(
              Object.assign(new Error("Request failed"), { variables })
            ),
        }),
      { wrapper: wrapperFor(queryClient) }
    )

    result.current.mutate({ patientName: "Jane Doe" })

    await waitFor(() => expect(showToast).toHaveBeenCalledTimes(1))
    expect(showToast).toHaveBeenCalledWith("Request failed")
    const [[message]] = showToast.mock.calls
    expect(message).not.toContain("Jane Doe")
  })

  it("still fires the global handler when the mutation defines its own onError", async () => {
    const showToast = vi.fn()
    const localOnError = vi.fn()
    const queryClient = createAppQueryClient(showToast)

    const { result } = renderHook(
      () =>
        useMutation({
          mutationFn: () => Promise.reject(new Error("boom")),
          onError: localOnError,
        }),
      { wrapper: wrapperFor(queryClient) }
    )

    result.current.mutate(undefined)

    await waitFor(() => expect(showToast).toHaveBeenCalledTimes(1))
    expect(localOnError).toHaveBeenCalledTimes(1)
  })
})
