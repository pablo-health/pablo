// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * How often draft notices re-read the session list.
 *
 * The dashboard layout mounts the notices on every page, so this list read
 * runs for every minute someone is signed in. It re-reads every three
 * minutes, at once when the tab regains focus (even while the last read is
 * still fresh), and never while the tab is hidden. That its requests are
 * background requests is pinned in pollingRequests.test.tsx.
 */

import type { ReactNode } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query"
import * as sessionsApi from "@/lib/api/sessions"
import { queryKeys } from "@/lib/api/queryKeys"
import { resetWatchedDrafts } from "@/lib/draftWatch"
import { useDraftNotices } from "../useDraftNotices"

vi.mock("@/lib/api/sessions")
vi.mock("@/lib/auth/provider", () => ({
  getClientAuthProvider: () => ({ getIdToken: () => Promise.resolve("tok") }),
}))
vi.mock("@/lib/config", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/config")>()),
  useConfig: () => ({ dataMode: "api" }),
}))

/** The app's own defaults: data stays fresh a minute, no refetch on focus. */
const newQueryClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 60 * 1000, refetchOnWindowFocus: false },
    },
  })

function createWrapper(queryClient: QueryClient) {
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "QueryWrapper"
  return Wrapper
}

function refocusTab() {
  act(() => {
    focusManager.setFocused(false)
    focusManager.setFocused(true)
  })
}

describe("draft notices: session list reads", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(sessionsApi.listSessions).mockResolvedValue({
      data: [],
      total: 0,
      page: 1,
      page_size: 20,
    })
  })

  afterEach(() => {
    // Hand focus tracking back to the browser's own events.
    focusManager.setFocused(undefined)
    resetWatchedDrafts()
  })

  it("re-reads the list every three minutes, and not while the tab is hidden", async () => {
    const queryClient = newQueryClient()
    renderHook(useDraftNotices, { wrapper: createWrapper(queryClient) })
    await waitFor(() => expect(sessionsApi.listSessions).toHaveBeenCalledTimes(1))

    const query = queryClient.getQueryCache().find({ queryKey: queryKeys.sessions.list() })
    const observed = query?.observers[0]?.options
    expect(observed?.refetchInterval).toBe(180_000)
    expect(observed?.refetchIntervalInBackground).toBe(false)
  })

  it("re-reads the list when the tab regains focus, inside the stale window", async () => {
    const queryClient = newQueryClient()
    renderHook(useDraftNotices, { wrapper: createWrapper(queryClient) })
    await waitFor(() => expect(sessionsApi.listSessions).toHaveBeenCalledTimes(1))

    refocusTab()

    await waitFor(() => expect(sessionsApi.listSessions).toHaveBeenCalledTimes(2))
  })
})
