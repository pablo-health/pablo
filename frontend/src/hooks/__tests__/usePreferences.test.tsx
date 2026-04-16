// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { usePreferences, useSavePreferences } from "../usePreferences"
import * as usersApi from "@/lib/api/users"

vi.mock("@/lib/api/users")
vi.mock("@/lib/config", () => ({
  useConfig: () => ({ dataMode: "api" }),
}))

const mockPreferences: usersApi.UserPreferences = {
  default_video_platform: "zoom",
  default_session_type: "individual",
  default_duration_minutes: 50,
  auto_transcribe: true,
  quality_preset: "balanced",
  therapist_display_name: null,
  working_hours_start: 8,
  working_hours_end: 18,
  calendar_default_view: "week",
  timezone: "America/New_York",
}

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

describe("usePreferences", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("fetches preferences on mount", async () => {
    vi.mocked(usersApi.getPreferences).mockResolvedValue(mockPreferences)

    const { result } = renderHook(() => usePreferences(), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual(mockPreferences)
    expect(usersApi.getPreferences).toHaveBeenCalledOnce()
  })

  it("returns working hours defaults from backend", async () => {
    vi.mocked(usersApi.getPreferences).mockResolvedValue(mockPreferences)

    const { result } = renderHook(() => usePreferences(), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.working_hours_start).toBe(8)
    expect(result.current.data?.working_hours_end).toBe(18)
  })

  it("returns custom working hours", async () => {
    vi.mocked(usersApi.getPreferences).mockResolvedValue({
      ...mockPreferences,
      working_hours_start: 10,
      working_hours_end: 20,
    })

    const { result } = renderHook(() => usePreferences(), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.working_hours_start).toBe(10)
    expect(result.current.data?.working_hours_end).toBe(20)
  })
})

describe("useSavePreferences", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("saves preferences and updates cache", async () => {
    const updated = { ...mockPreferences, working_hours_start: 9 }
    vi.mocked(usersApi.savePreferences).mockResolvedValue(updated)

    const { result } = renderHook(() => useSavePreferences(), {
      wrapper: createWrapper(),
    })

    result.current.mutate(updated)

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(usersApi.savePreferences).toHaveBeenCalledWith(updated, undefined)
  })
})
