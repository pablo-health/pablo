// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Appointment React Query Hook Tests
 *
 * Tests hooks with real QueryClient, mock API functions. Covers the
 * create-appointment cache write (writing the mutation response directly
 * into the matching cached list range) and the narrowed invalidation.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"
import {
  useAppointmentList,
  useCreateAppointment,
  useCreateRecurringAppointment,
} from "../useAppointments"
import * as schedulingApi from "@/lib/api/scheduling"
import { queryKeys } from "@/lib/api/queryKeys"
import type { AppointmentListResponse, AppointmentResponse } from "@/types/scheduling"

vi.mock("@/lib/api/scheduling")

function makeAppointment(overrides: Partial<AppointmentResponse> = {}): AppointmentResponse {
  return {
    id: "appt-1",
    user_id: "user-1",
    patient_id: "patient-1",
    title: "Therapy session",
    patient_name: "Jane Doe",
    start_at: "2026-01-05T15:00:00Z",
    end_at: "2026-01-05T15:50:00Z",
    duration_minutes: 50,
    status: "confirmed",
    session_type: "individual",
    video_link: null,
    video_platform: null,
    notes: null,
    note_type: "soap",
    recurrence_rule: null,
    recurring_appointment_id: null,
    recurrence_index: null,
    is_exception: false,
    google_event_id: null,
    google_sync_status: null,
    session_id: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: null,
    ...overrides,
  }
}

function createWrapper(queryClient: QueryClient) {
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "QueryWrapper"
  return Wrapper
}

const newQueryClient = () =>
  new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })

describe("useAppointments hooks", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe("useCreateAppointment", () => {
    it("writes the mutation response into a cached list range that contains it", async () => {
      const queryClient = newQueryClient()
      const containingRange = { start: "2026-01-04T00:00:00Z", end: "2026-01-11T00:00:00Z" }
      const existing = makeAppointment({ id: "appt-existing", patient_name: "Existing Patient" })
      queryClient.setQueryData<AppointmentListResponse>(
        queryKeys.appointments.list(containingRange),
        { data: [existing], total: 1 },
      )

      const created = makeAppointment({ id: "appt-new", patient_name: "Newly Booked Patient" })
      vi.mocked(schedulingApi.createAppointment).mockResolvedValue(created)

      const { result } = renderHook(() => useCreateAppointment(), {
        wrapper: createWrapper(queryClient),
      })

      await result.current.mutateAsync({
        patient_id: "patient-1",
        title: "Therapy session",
        start_at: created.start_at,
        end_at: created.end_at,
        duration_minutes: 50,
      })

      const cached = queryClient.getQueryData<AppointmentListResponse>(
        queryKeys.appointments.list(containingRange),
      )
      expect(cached?.data.map((a) => a.patient_name)).toEqual([
        "Existing Patient",
        "Newly Booked Patient",
      ])
      expect(cached?.total).toBe(2)

      // The new appointment must come from the mutation response, not a refetch.
      expect(schedulingApi.listAppointments).not.toHaveBeenCalled()
    })

    it("leaves a cached list range that does not contain the appointment untouched", async () => {
      const queryClient = newQueryClient()
      const otherRange = { start: "2026-02-01T00:00:00Z", end: "2026-02-08T00:00:00Z" }
      const otherEntry: AppointmentListResponse = {
        data: [makeAppointment({ id: "appt-other" })],
        total: 1,
      }
      queryClient.setQueryData(queryKeys.appointments.list(otherRange), otherEntry)

      const created = makeAppointment({ id: "appt-new", start_at: "2026-01-05T15:00:00Z" })
      vi.mocked(schedulingApi.createAppointment).mockResolvedValue(created)

      const { result } = renderHook(() => useCreateAppointment(), {
        wrapper: createWrapper(queryClient),
      })

      await result.current.mutateAsync({
        patient_id: "patient-1",
        title: "Therapy session",
        start_at: created.start_at,
        end_at: created.end_at,
        duration_minutes: 50,
      })

      const cached = queryClient.getQueryData(queryKeys.appointments.list(otherRange))
      expect(cached).toBe(otherEntry)
    })

    it("does not invalidate appointments.all, leaving unrelated detail queries intact", async () => {
      const queryClient = newQueryClient()
      const detailKey = queryKeys.appointments.detail("unrelated-appointment")
      const detailData = makeAppointment({ id: "unrelated-appointment" })
      queryClient.setQueryData(detailKey, detailData)

      vi.mocked(schedulingApi.createAppointment).mockResolvedValue(makeAppointment())

      const { result } = renderHook(() => useCreateAppointment(), {
        wrapper: createWrapper(queryClient),
      })

      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries")

      await result.current.mutateAsync({
        patient_id: "patient-1",
        title: "Therapy session",
        start_at: "2026-01-05T15:00:00Z",
        end_at: "2026-01-05T15:50:00Z",
        duration_minutes: 50,
      })

      expect(invalidateSpy).not.toHaveBeenCalledWith({
        queryKey: queryKeys.appointments.all,
      })
      expect(queryClient.getQueryState(detailKey)?.isInvalidated).toBeFalsy()
      expect(queryClient.getQueryData(detailKey)).toEqual(detailData)
    })

    // The load-bearing one. Every other test here seeds the cache by hand and
    // never mounts a list observer, so none of them exercise what the calendar
    // actually does: hold an ACTIVE useAppointmentList while a create resolves.
    // The appointment appearing on the grid is the whole feature, and until now
    // nothing asserted it end to end at the hook seam.
    it("an active list observer sees the created appointment without a reload", async () => {
      const queryClient = newQueryClient()
      const range = { start: "2026-01-04T00:00:00Z", end: "2026-01-11T00:00:00Z" }
      vi.mocked(schedulingApi.listAppointments).mockResolvedValue({ data: [], total: 0 })

      const wrapper = createWrapper(queryClient)
      const { result } = renderHook(
        () => ({
          list: useAppointmentList(range.start, range.end),
          create: useCreateAppointment(),
        }),
        { wrapper },
      )
      await waitFor(() => expect(result.current.list.isSuccess).toBe(true))
      expect(result.current.list.data?.data).toHaveLength(0)

      const created = makeAppointment({ start_at: "2026-01-05T15:00:00Z" })
      vi.mocked(schedulingApi.createAppointment).mockResolvedValue(created)
      vi.mocked(schedulingApi.listAppointments).mockResolvedValue({
        data: [created],
        total: 1,
      })

      await result.current.create.mutateAsync({
        patient_id: "patient-1",
        title: "Therapy session",
        start_at: created.start_at,
        end_at: created.end_at,
        duration_minutes: 50,
      })

      await waitFor(() =>
        expect(result.current.list.data?.data.map((a) => a.id)).toEqual([created.id]),
      )
    })

    // The append is an optimisation; the invalidation is what makes the grid
    // agree with the server. #742 shipped the former and dropped the latter,
    // leaving one mechanism with no backstop — assert both are wired.
    it("also invalidates the lists so the grid reconciles with the server", async () => {
      const queryClient = newQueryClient()
      vi.mocked(schedulingApi.createAppointment).mockResolvedValue(makeAppointment())

      const { result } = renderHook(() => useCreateAppointment(), {
        wrapper: createWrapper(queryClient),
      })
      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries")

      await result.current.mutateAsync({
        patient_id: "patient-1",
        title: "Therapy session",
        start_at: "2026-01-05T15:00:00Z",
        end_at: "2026-01-05T15:50:00Z",
        duration_minutes: 50,
      })

      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: queryKeys.appointments.lists(),
      })
    })
  })

  describe("useCreateRecurringAppointment", () => {
    it("invalidates appointment lists, not the whole appointments cache", async () => {
      const queryClient = newQueryClient()
      const detailKey = queryKeys.appointments.detail("unrelated-appointment")
      queryClient.setQueryData(detailKey, makeAppointment({ id: "unrelated-appointment" }))

      vi.mocked(schedulingApi.createRecurringAppointment).mockResolvedValue({
        data: [makeAppointment()],
        total: 1,
      })

      const { result } = renderHook(() => useCreateRecurringAppointment(), {
        wrapper: createWrapper(queryClient),
      })

      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries")

      await result.current.mutateAsync({
        patient_id: "patient-1",
        title: "Therapy session",
        start_at: "2026-01-05T15:00:00Z",
        end_at: "2026-01-05T15:50:00Z",
        duration_minutes: 50,
        frequency: "weekly",
        timezone: "UTC",
      })

      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: queryKeys.appointments.lists(),
      })
      expect(invalidateSpy).not.toHaveBeenCalledWith({
        queryKey: queryKeys.appointments.all,
      })
      expect(queryClient.getQueryState(detailKey)?.isInvalidated).toBeFalsy()
    })
  })
})
