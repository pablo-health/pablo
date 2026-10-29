// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { useAudioRetention, useAudioRetentionSetting } from "../useAudioRetention"
import * as practicesApi from "@/lib/api/practices"

vi.mock("@/lib/api/practices")
vi.mock("@/lib/config", () => ({
  useConfig: () => ({ dataMode: "api" }),
}))
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ loading: false }),
}))

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "TestQueryClientWrapper"
  return Wrapper
}

describe("useAudioRetentionSetting", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("reads the practice's setting", async () => {
    vi.mocked(practicesApi.getAudioRetention).mockResolvedValue({
      practice_id: "prac_1",
      audio_retention_days: 0,
    })

    const { result } = renderHook(() => useAudioRetentionSetting(), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.audio_retention_days).toBe(0)
  })
})

describe("useAudioRetention", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("calls updateAudioRetention with the days", async () => {
    vi.mocked(practicesApi.updateAudioRetention).mockResolvedValue({
      practice_id: "prac_1",
      audio_retention_days: 500,
    })

    const { result } = renderHook(() => useAudioRetention(), {
      wrapper: createWrapper(),
    })

    result.current.mutate({ days: 500 })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(practicesApi.updateAudioRetention).toHaveBeenCalledWith(500, undefined)
    expect(result.current.data).toEqual({
      practice_id: "prac_1",
      audio_retention_days: 500,
    })
  })

  it("surfaces errors from the API", async () => {
    vi.mocked(practicesApi.updateAudioRetention).mockRejectedValue(new Error("boom"))

    const { result } = renderHook(() => useAudioRetention(), {
      wrapper: createWrapper(),
    })

    result.current.mutate({ days: 100 })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.error?.message).toBe("boom")
  })

  it("forwards the optional token to the API client", async () => {
    vi.mocked(practicesApi.updateAudioRetention).mockResolvedValue({
      practice_id: "prac_1",
      audio_retention_days: 90,
    })

    const { result } = renderHook(() => useAudioRetention("tok-1"), {
      wrapper: createWrapper(),
    })

    result.current.mutate({ days: 90 })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(practicesApi.updateAudioRetention).toHaveBeenCalledWith(90, "tok-1")
  })
})
