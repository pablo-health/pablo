// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { AppointmentResponse } from "@/types/scheduling"
import { TodayPanel, formatLastVisit } from "../TodayPanel"
import { createMockPatient } from "@/test/factories"

const useAppointmentList = vi.hoisted(() => vi.fn())
const usePatientList = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useAppointments", () => ({
  useAppointmentList: (...args: unknown[]) => useAppointmentList(...args),
}))

vi.mock("@/hooks/usePatients", () => ({
  usePatientList: (...args: unknown[]) => usePatientList(...args),
}))

vi.mock("@/hooks/usePreferences", () => ({
  useUserTimeZone: () => "America/New_York",
}))

function makeAppointment(
  overrides: Partial<AppointmentResponse> = {},
): AppointmentResponse {
  return {
    id: "appt-1",
    user_id: "user-1",
    patient_id: "patient-1",
    title: "Jordan Rivera",
    start_at: "2026-05-07T13:00:00Z",
    end_at: "2026-05-07T13:50:00Z",
    duration_minutes: 50,
    status: "confirmed",
    session_type: "individual",
    video_link: null,
    video_platform: null,
    notes: null,
    recurrence_rule: null,
    recurring_appointment_id: null,
    recurrence_index: null,
    is_exception: false,
    google_event_id: null,
    google_sync_status: null,
    session_id: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: null,
    ...overrides,
  }
}

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <TodayPanel />
    </QueryClientProvider>,
  )
}

describe("TodayPanel", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-05-07T08:00:00Z"))
    usePatientList.mockReturnValue({ data: { data: [] } })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it("shows the empty state with Pablo when there are no appointments", () => {
    useAppointmentList.mockReturnValue({ data: { data: [] }, isLoading: false })

    renderPanel()

    expect(screen.getByText(/no sessions today/i)).toBeInTheDocument()
    expect(screen.getByAltText(/pablo bear/i)).toBeInTheDocument()
  })

  it("renders appointments sorted by start time", () => {
    useAppointmentList.mockReturnValue({
      data: {
        data: [
          makeAppointment({
            id: "a-late",
            title: "Afternoon",
            start_at: "2026-05-07T18:00:00Z",
          }),
          makeAppointment({
            id: "a-early",
            title: "Morning",
            start_at: "2026-05-07T09:00:00Z",
          }),
        ],
      },
      isLoading: false,
    })

    renderPanel()

    const items = screen.getAllByRole("listitem")
    expect(items[0]).toHaveTextContent("Morning")
    expect(items[1]).toHaveTextContent("Afternoon")
  })

  it("emits a pablohealth:// deep link for confirmed appointments without a session", () => {
    useAppointmentList.mockReturnValue({
      data: { data: [makeAppointment({ id: "appt-x" })] },
      isLoading: false,
    })

    renderPanel()

    const startLink = screen.getByRole("link", { name: /start session/i })
    expect(startLink).toHaveAttribute(
      "href",
      "pablohealth://session/start?appointment=appt-x",
    )
  })

  it("links already-recorded appointments to the web session detail", () => {
    useAppointmentList.mockReturnValue({
      data: {
        data: [
          makeAppointment({
            status: "completed",
            session_id: "sess-99",
          }),
        ],
      },
      isLoading: false,
    })

    renderPanel()

    const open = screen.getByRole("link", { name: /^open$/i })
    expect(open).toHaveAttribute("href", "/dashboard/sessions/sess-99")
  })

  it("surfaces last-visit hint when patient data resolves it", () => {
    useAppointmentList.mockReturnValue({
      data: { data: [makeAppointment({ patient_id: "patient-7" })] },
      isLoading: false,
    })
    usePatientList.mockReturnValue({
      data: {
        data: [
          createMockPatient({
            id: "patient-7",
            // 4 weeks before the appointment.
            last_session_date: "2026-04-09T13:00:00Z",
          }),
        ],
      },
    })

    renderPanel()

    expect(screen.getByText(/last visit 4w ago/i)).toBeInTheDocument()
  })
})

describe("formatLastVisit", () => {
  const futureAppt = "2026-05-07T13:00:00Z"

  it("returns null when there's no prior session", () => {
    expect(formatLastVisit(null, futureAppt)).toBeNull()
  })

  it("returns null when last visit is after the appointment (data churn)", () => {
    expect(formatLastVisit("2026-06-01T00:00:00Z", futureAppt)).toBeNull()
  })

  it("uses 'yesterday' for one day ago", () => {
    expect(formatLastVisit("2026-05-06T13:00:00Z", futureAppt)).toBe(
      "last visit yesterday",
    )
  })

  it("uses days for under a week", () => {
    expect(formatLastVisit("2026-05-04T13:00:00Z", futureAppt)).toBe(
      "last visit 3d ago",
    )
  })

  it("uses weeks for under two months", () => {
    expect(formatLastVisit("2026-04-09T13:00:00Z", futureAppt)).toBe(
      "last visit 4w ago",
    )
  })

  it("falls back to months for older visits", () => {
    expect(formatLastVisit("2025-12-01T13:00:00Z", futureAppt)).toBe(
      "last visit 5mo ago",
    )
  })
})
