// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { useCompanionDevices } from "../useCompanionDevices"
import { ApiError } from "@/lib/api/client"
import * as devicesApi from "@/lib/api/devices"

vi.mock("@/lib/api/devices")
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ loading: false }),
}))

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "TestQueryClientWrapper"
  return Wrapper
}

describe("useCompanionDevices", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("returns the enrolled devices", async () => {
    const device: devicesApi.CompanionDevice = {
      install_id: "install-1",
      platform: "mac",
      os_version: "15.2",
      enrolled_at: "2026-10-01T00:00:00Z",
      last_seen: "2026-10-01T00:00:00Z",
      jkt_fingerprint: "abcdefabcdef",
    }
    vi.mocked(devicesApi.listCompanionDevices).mockResolvedValue([device])

    const { result } = renderHook(() => useCompanionDevices(), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([device])
  })

  it("treats a missing endpoint as no devices", async () => {
    vi.mocked(devicesApi.listCompanionDevices).mockRejectedValue(
      new ApiError("NOT_FOUND", "Not found", undefined, 404),
    )

    const { result } = renderHook(() => useCompanionDevices(), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([])
  })

  it.each([
    ["an auth failure", new ApiError("UNAUTHORIZED", "No", undefined, 401)],
    ["a server error", new ApiError("UNKNOWN_ERROR", "Boom", undefined, 500)],
    ["a network failure", new TypeError("Failed to fetch")],
  ])("reports %s as an error, not as no devices", async (_label, err) => {
    vi.mocked(devicesApi.listCompanionDevices).mockRejectedValue(err)

    const { result } = renderHook(() => useCompanionDevices(), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.data).toBeUndefined()
  })
})
