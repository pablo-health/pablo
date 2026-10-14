// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { usePreferences, useUpdatePreferences } from "../usePreferences"
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
  calendar_default_view: "week",
  timezone: "America/New_York",
  theme: "warm-paper",
  calendar_density: "balanced",
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
})

describe("useUpdatePreferences", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("sends only the change and caches everything the server returns", async () => {
    const merged = { ...mockPreferences, therapist_display_name: "Dr. Rivera" }
    vi.mocked(usersApi.getPreferences).mockResolvedValue(mockPreferences)
    vi.mocked(usersApi.updatePreferences).mockResolvedValue(merged)

    const { result } = renderHook(
      () => ({ update: useUpdatePreferences(), cached: usePreferences() }),
      { wrapper: createWrapper() },
    )
    await waitFor(() => expect(result.current.cached.isSuccess).toBe(true))

    result.current.update.mutate({ therapist_display_name: "Dr. Rivera" })

    await waitFor(() => expect(result.current.update.isSuccess).toBe(true))
    expect(usersApi.updatePreferences).toHaveBeenCalledWith(
      { therapist_display_name: "Dr. Rivera" },
      undefined,
    )
    expect(result.current.cached.data).toEqual(merged)
  })
})
