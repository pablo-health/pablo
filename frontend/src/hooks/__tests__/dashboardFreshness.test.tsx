// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The dashboard is one cached summary under its own key. These tests run each
 * change that alters what it shows — today's appointments, the week's count,
 * sessions transcribing or awaiting review, notes awaiting signature — and
 * check the cached summary is marked stale (and refetched, when a dashboard
 * is on screen), so a new appointment shows up without a reload.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { act, renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query"
import type { ReactNode } from "react"
import {
  useCancelAppointment,
  useCancelAppointmentSeries,
  useCreateAppointment,
  useCreateRecurringAppointment,
  useEditAppointmentSeries,
  useUpdateAppointment,
} from "../useAppointments"
import {
  useAnswerOutsideSessions,
  useSyncCalendarsNow,
  useUndoAutoBooked,
} from "../useOutsideSessions"
import { useResolveGoogleChange, useResolveHeldGoogleRemovals } from "../useGoogleCalendarChanges"
import { useFinalizeSession, useUpdateSessionMetadata, useUploadSession } from "../useSessions"
import { useFinalizeNote } from "../useNotes"
import { useSignNote, useUnlockNote } from "../useNoteSigning"
import { useDeletePatient, useRestorePatient } from "../usePatients"
import { useImportNotes } from "../useImportNotes"
import * as schedulingApi from "@/lib/api/scheduling"
import * as sessionsApi from "@/lib/api/sessions"
import * as notesApi from "@/lib/api/notes"
import * as outsideSessionsApi from "@/lib/api/outsideSessions"
import { queryKeys } from "@/lib/api/queryKeys"
import type { AppointmentResponse, CreateAppointmentRequest } from "@/types/scheduling"
import type { Note } from "@/types/notes"
import type { SessionResponse } from "@/types/sessions"
import type { DashboardSummary } from "@/types/dashboard"

vi.mock("@/lib/api/scheduling")
vi.mock("@/lib/api/sessions")
vi.mock("@/lib/api/notes")
vi.mock("@/lib/api/patients")
vi.mock("@/lib/api/outsideSessions")
vi.mock("@/lib/config", () => ({ useConfig: () => ({ dataMode: "api" }) }))

const SUMMARY_KEY = queryKeys.dashboard.summary({
  today: "2026-01-05T05:00:00.000Z",
  week: "2026-01-12T05:00:00.000Z",
})

const EMPTY_SUMMARY: DashboardSummary = {
  today_appointments: [],
  last_visit_by_patient: {},
  week_confirmed_count: 0,
  notes_pending_count: 0,
  transcription_pending_count: 0,
  awaiting_review_total: 0,
  awaiting_review: [],
}

const APPOINTMENT = {
  id: "appt-1",
  patient_id: "patient-1",
  start_at: "2026-01-05T15:00:00Z",
  end_at: "2026-01-05T15:50:00Z",
} as AppointmentResponse

const SESSION = { id: "session-1", patient_id: "patient-1" } as SessionResponse
const NOTE = { id: "note-1", patient_id: "patient-1" } as Note

const newQueryClient = () =>
  new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })

function wrapperFor(queryClient: QueryClient) {
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "QueryWrapper"
  return Wrapper
}

type Run = (queryClient: QueryClient) => Promise<void>

/** Render a mutation hook and run it once with `variables`. */
function mutation<TVariables>(
  useHook: () => { mutateAsync: (variables: TVariables) => Promise<unknown> },
  variables: TVariables,
): Run {
  return async (queryClient) => {
    const { result } = renderHook(useHook, { wrapper: wrapperFor(queryClient) })
    await act(async () => {
      await result.current.mutateAsync(variables)
    })
  }
}

const CREATE_REQUEST: CreateAppointmentRequest = {
  patient_id: "patient-1",
  title: "Therapy session",
  start_at: APPOINTMENT.start_at,
  end_at: APPOINTMENT.end_at,
  duration_minutes: 50,
}

const CASES: Array<{ name: string; run: Run }> = [
  { name: "create an appointment", run: mutation(useCreateAppointment, CREATE_REQUEST) },
  {
    name: "create a recurring series",
    run: mutation(useCreateRecurringAppointment, {
      ...CREATE_REQUEST,
      frequency: "weekly",
      timezone: "America/New_York",
      count: 4,
    }),
  },
  {
    name: "update an appointment (move, no-show, complete)",
    run: mutation(useUpdateAppointment, {
      appointmentId: "appt-1",
      data: { status: "no_show" },
    }),
  },
  { name: "cancel an appointment", run: mutation(useCancelAppointment, "appt-1") },
  {
    name: "edit a series",
    run: mutation(useEditAppointmentSeries, {
      appointmentId: "appt-1",
      data: { title: "Therapy session" },
    }),
  },
  { name: "cancel a series", run: mutation(useCancelAppointmentSeries, "appt-1") },
  { name: "undo an automatic booking", run: mutation(useUndoAutoBooked, "appt-1") },
  { name: "answer calendar questions", run: mutation(useAnswerOutsideSessions, []) },
  { name: "read calendars now", run: mutation(useSyncCalendarsNow, undefined) },
  {
    name: "settle a Google Calendar change",
    run: mutation(useResolveGoogleChange, { appointmentId: "appt-1", resolution: "keep_pablo" }),
  },
  {
    name: "settle held Google Calendar removals",
    run: mutation(useResolveHeldGoogleRemovals, "keep_pablo"),
  },
  {
    name: "upload a session",
    run: mutation(useUploadSession, {
      patientId: "patient-1",
      data: {} as Parameters<typeof sessionsApi.uploadSession>[1],
    }),
  },
  {
    name: "finalize a session",
    run: mutation(useFinalizeSession, { sessionId: "session-1", data: {} }),
  },
  {
    name: "change a session's date",
    run: mutation(useUpdateSessionMetadata, {
      sessionId: "session-1",
      data: { session_date: "2026-01-04T15:00:00Z" },
    }),
  },
  {
    name: "finalize a note",
    run: mutation(useFinalizeNote, { noteId: "note-1", data: {} }),
  },
  {
    name: "sign a note",
    run: mutation(useSignNote, {
      noteId: "note-1",
      data: {} as Parameters<typeof notesApi.signNote>[1],
    }),
  },
  {
    name: "unlock a note",
    run: mutation(useUnlockNote, {
      noteId: "note-1",
      data: {} as Parameters<typeof notesApi.unlockNote>[1],
    }),
  },
  {
    name: "delete a client",
    run: mutation(useDeletePatient, {
      patientId: "patient-1",
      acknowledgedRetentionObligation: true,
    }),
  },
  { name: "restore a client", run: mutation(useRestorePatient, "patient-1") },
  {
    name: "import notes",
    run: async (queryClient) => {
      const { result } = renderHook(() => useImportNotes("patient-1"), {
        wrapper: wrapperFor(queryClient),
      })
      await act(async () => {
        await result.current.start([new File(["x"], "a.pdf", { type: "application/pdf" })])
      })
    },
  },
]

describe("dashboard freshness", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(schedulingApi.createAppointment).mockResolvedValue(APPOINTMENT)
    vi.mocked(schedulingApi.updateAppointment).mockResolvedValue(APPOINTMENT)
    vi.mocked(sessionsApi.uploadSession).mockResolvedValue(SESSION)
    vi.mocked(sessionsApi.finalizeSession).mockResolvedValue(SESSION)
    vi.mocked(sessionsApi.updateSessionMetadata).mockResolvedValue(SESSION)
    vi.mocked(sessionsApi.importNote).mockResolvedValue(SESSION)
    vi.mocked(notesApi.finalizeNote).mockResolvedValue(NOTE)
    vi.mocked(notesApi.signNote).mockResolvedValue(NOTE)
    vi.mocked(notesApi.unlockNote).mockResolvedValue(NOTE)
    vi.mocked(outsideSessionsApi.answerOutsideSessions).mockResolvedValue({
      appointments: [],
      not_added: [],
    } as unknown as Awaited<ReturnType<typeof outsideSessionsApi.answerOutsideSessions>>)
  })

  it.each(CASES)("$name marks the cached dashboard summary stale", async ({ run }) => {
    const queryClient = newQueryClient()
    queryClient.setQueryData(SUMMARY_KEY, EMPTY_SUMMARY)
    expect(queryClient.getQueryState(SUMMARY_KEY)?.isInvalidated).toBe(false)

    await run(queryClient)

    await waitFor(() =>
      expect(queryClient.getQueryState(SUMMARY_KEY)?.isInvalidated).toBe(true),
    )
  })

  it("refetches a dashboard that is on screen when an appointment is added", async () => {
    const queryClient = newQueryClient()
    const withAppointment: DashboardSummary = {
      ...EMPTY_SUMMARY,
      today_appointments: [APPOINTMENT],
    }
    const fetchSummary = vi
      .fn<() => Promise<DashboardSummary>>()
      .mockResolvedValueOnce(EMPTY_SUMMARY)
      .mockResolvedValue(withAppointment)
    const dashboard = renderHook(
      () => useQuery({ queryKey: SUMMARY_KEY, queryFn: fetchSummary, staleTime: 60 * 1000 }),
      { wrapper: wrapperFor(queryClient) },
    )
    await waitFor(() => expect(dashboard.result.current.data).toEqual(EMPTY_SUMMARY))

    await mutation(useCreateAppointment, CREATE_REQUEST)(queryClient)

    await waitFor(() =>
      expect(dashboard.result.current.data?.today_appointments).toEqual([APPOINTMENT]),
    )
    expect(fetchSummary).toHaveBeenCalledTimes(2)
  })
})
