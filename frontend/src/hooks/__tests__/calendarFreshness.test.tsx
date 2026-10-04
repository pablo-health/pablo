// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The calendar's queries keep up with calendar reads.
 *
 * A read on the server can move, add or cancel sessions while the calendar
 * is open. These pin the three ways the open calendar catches up: refetching
 * when the tab regains focus (even inside the stale window), a one-minute
 * interval while mounted, and an invalidation after a read the clinician
 * asked for or an answered "who is this?" question.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query"
import type { ReactNode } from "react"
import { useAppointmentList } from "../useAppointments"
import { useHeldGoogleRemovals } from "../useGoogleCalendarChanges"
import {
  useAnswerOutsideSessions,
  useOutsideQuestions,
  useOutsideSessions,
  useSyncCalendarsNow,
} from "../useOutsideSessions"
import { CALENDAR_REFETCH_INTERVAL_MS } from "../calendarFreshness"
import * as schedulingApi from "@/lib/api/scheduling"
import * as outsideApi from "@/lib/api/outsideSessions"
import { queryKeys } from "@/lib/api/queryKeys"

vi.mock("@/lib/api/scheduling")
vi.mock("@/lib/api/outsideSessions")

const RANGE = { start: "2026-01-04T00:00:00Z", end: "2026-01-11T00:00:00Z" }

/** The app's own defaults: data stays fresh a minute, no refetch on focus. */
const newQueryClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 60 * 1000, refetchOnWindowFocus: false },
      mutations: { retry: false },
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

describe("calendar query freshness", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(schedulingApi.listAppointments).mockResolvedValue({ data: [], total: 0 })
    vi.mocked(schedulingApi.getHeldGoogleRemovals).mockResolvedValue({ count: 0 })
    vi.mocked(outsideApi.listOutsideSessions).mockResolvedValue({ events: [] })
    vi.mocked(outsideApi.getOutsideQuestions).mockResolvedValue(
      {} as Awaited<ReturnType<typeof outsideApi.getOutsideQuestions>>
    )
  })

  afterEach(() => {
    // Hand focus tracking back to the browser's own events.
    focusManager.setFocused(undefined)
  })

  describe("on focus", () => {
    it("refetches the week's appointments when the tab regains focus, inside the stale window", async () => {
      const queryClient = newQueryClient()
      const { result } = renderHook(() => useAppointmentList(RANGE.start, RANGE.end), {
        wrapper: createWrapper(queryClient),
      })
      await waitFor(() => expect(result.current.isSuccess).toBe(true))
      expect(schedulingApi.listAppointments).toHaveBeenCalledTimes(1)

      refocusTab()

      await waitFor(() => expect(schedulingApi.listAppointments).toHaveBeenCalledTimes(2))
    })

    it("refetches the calendar's open events, questions and held removals on focus too", async () => {
      const queryClient = newQueryClient()
      const { result } = renderHook(
        () => ({
          outside: useOutsideSessions(RANGE.start, RANGE.end),
          questions: useOutsideQuestions(),
          held: useHeldGoogleRemovals(),
        }),
        { wrapper: createWrapper(queryClient) }
      )
      await waitFor(() => {
        expect(result.current.outside.isSuccess).toBe(true)
        expect(result.current.questions.isSuccess).toBe(true)
        expect(result.current.held.isSuccess).toBe(true)
      })

      refocusTab()

      await waitFor(() => {
        expect(outsideApi.listOutsideSessions).toHaveBeenCalledTimes(2)
        expect(outsideApi.getOutsideQuestions).toHaveBeenCalledTimes(2)
        expect(schedulingApi.getHeldGoogleRemovals).toHaveBeenCalledTimes(2)
      })
    })
  })

  describe("on an interval", () => {
    it("polls every minute while mounted, and not while the tab is hidden", async () => {
      const queryClient = newQueryClient()
      const { result } = renderHook(() => useAppointmentList(RANGE.start, RANGE.end), {
        wrapper: createWrapper(queryClient),
      })
      await waitFor(() => expect(result.current.isSuccess).toBe(true))

      const query = queryClient.getQueryCache().find({
        queryKey: queryKeys.appointments.list(RANGE),
      })
      expect(query?.options).toMatchObject({
        refetchInterval: CALENDAR_REFETCH_INTERVAL_MS,
        refetchIntervalInBackground: false,
      })
      expect(CALENDAR_REFETCH_INTERVAL_MS).toBe(60 * 1000)
    })
  })

  describe("after a read or an answer", () => {
    it("refetches the open week after a read the clinician asked for succeeds", async () => {
      vi.mocked(outsideApi.syncCalendarsNow).mockResolvedValue({
        ical_sources_synced: 0,
        ical_errors: 0,
        google_synced: true,
        google_error: false,
        google_changes_processed: 1,
        outside_sessions_followed: 0,
        reminders_sent: 0,
      })
      const queryClient = newQueryClient()
      const { result } = renderHook(
        () => ({
          list: useAppointmentList(RANGE.start, RANGE.end),
          outside: useOutsideSessions(RANGE.start, RANGE.end),
          sync: useSyncCalendarsNow(),
        }),
        { wrapper: createWrapper(queryClient) }
      )
      await waitFor(() => {
        expect(result.current.list.isSuccess).toBe(true)
        expect(result.current.outside.isSuccess).toBe(true)
      })
      expect(schedulingApi.listAppointments).toHaveBeenCalledTimes(1)

      await act(async () => {
        await result.current.sync.mutateAsync()
      })

      expect(outsideApi.syncCalendarsNow).toHaveBeenCalledTimes(1)
      await waitFor(() => {
        expect(schedulingApi.listAppointments).toHaveBeenCalledTimes(2)
        expect(outsideApi.listOutsideSessions).toHaveBeenCalledTimes(2)
      })
    })

    it("leaves the week alone when the read fails", async () => {
      vi.mocked(outsideApi.syncCalendarsNow).mockRejectedValue(new Error("rate limited"))
      const queryClient = newQueryClient()
      const { result } = renderHook(
        () => ({
          list: useAppointmentList(RANGE.start, RANGE.end),
          sync: useSyncCalendarsNow(),
        }),
        { wrapper: createWrapper(queryClient) }
      )
      await waitFor(() => expect(result.current.list.isSuccess).toBe(true))

      await act(async () => {
        await result.current.sync.mutateAsync().catch(() => undefined)
      })

      expect(schedulingApi.listAppointments).toHaveBeenCalledTimes(1)
    })

    it("refetches the open week after outside-session questions are answered", async () => {
      vi.mocked(outsideApi.answerOutsideSessions).mockResolvedValue(
        {} as Awaited<ReturnType<typeof outsideApi.answerOutsideSessions>>
      )
      const queryClient = newQueryClient()
      const { result } = renderHook(
        () => ({
          list: useAppointmentList(RANGE.start, RANGE.end),
          answer: useAnswerOutsideSessions(),
        }),
        { wrapper: createWrapper(queryClient) }
      )
      await waitFor(() => expect(result.current.list.isSuccess).toBe(true))
      expect(schedulingApi.listAppointments).toHaveBeenCalledTimes(1)

      await act(async () => {
        await result.current.answer.mutateAsync([])
      })

      await waitFor(() => expect(schedulingApi.listAppointments).toHaveBeenCalledTimes(2))
    })
  })
})
