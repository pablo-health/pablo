// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The app's QueryClient wires a MutationCache-level onError so a mutation
 * that defines no onError of its own still surfaces its failure, instead
 * of failing silently.
 */
import { describe, it, expect, vi } from "vitest"
import { QueryClientProvider, useMutation } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import { createAppQueryClient } from "../providers"

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
