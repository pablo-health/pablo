// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { AppointmentResponse } from "@/types/scheduling"
import { TodayPanel, formatLastVisit } from "../TodayPanel"

const useDashboardSummary = vi.hoisted(() => vi.fn())
const useCompanionDevices = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useDashboard", () => ({
  useDashboardSummary: (...args: unknown[]) => useDashboardSummary(...args),
}))

vi.mock("@/hooks/useCompanionDevices", () => ({
  useCompanionDevices: (...args: unknown[]) => useCompanionDevices(...args),
}))

vi.mock("@/hooks/usePreferences", () => ({
  useUserTimeZone: () => "America/New_York",
}))

// jsdom has no navigator.platform — mock companion as available so tests
// that exercise the "Start session" deep link don't need to know about
// platform detection internals.
vi.mock("@/lib/companion", () => ({
  isMacOS: () => true,
  isCompanionAvailable: () => true,
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
    note_type: "soap",
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

function mockSummary(
  today_appointments: AppointmentResponse[],
  last_visit_by_patient: Record<string, string | null> = {},
  isLoading = false,
) {
  useDashboardSummary.mockReturnValue({
    data: { today_appointments, last_visit_by_patient },
    isLoading,
  })
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
    // Default: one enrolled companion install (Start Session path).
    useCompanionDevices.mockReturnValue({
      data: [{ install_id: "dev-1", platform: "mac" }],
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it("shows the empty state with Pablo when there are no appointments", () => {
    mockSummary([])

    renderPanel()

    expect(screen.getByText(/no sessions today/i)).toBeInTheDocument()
    expect(screen.getByAltText(/pablo bear/i)).toBeInTheDocument()
  })

  it("renders appointments sorted by start time", () => {
    mockSummary([
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
    ])

    renderPanel()

    const items = screen.getAllByRole("listitem")
    expect(items[0]).toHaveTextContent("Morning")
    expect(items[1]).toHaveTextContent("Afternoon")
  })

  it("shows Start session when a companion install is enrolled", () => {
    mockSummary([makeAppointment({ id: "appt-x" })])

    renderPanel()

    // The launch flow renders a real anchor whose href is the domain-verified
    // launch_url (prefetched on hover/focus). It's a link so macOS Safari
    // routes the Universal Link from an actual user-activated click — the old
    // pablohealth:// appointment-id anchor is gone.
    expect(
      screen.getByRole("link", { name: /start session/i }),
    ).toBeInTheDocument()
  })

  it("offers Download Pablo Companion when no install is enrolled", () => {
    useCompanionDevices.mockReturnValue({ data: [] })
    mockSummary([makeAppointment({ id: "appt-x" })])

    renderPanel()

    expect(
      screen.getByRole("button", { name: /download pablo companion/i }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /^start session$/i }),
    ).not.toBeInTheDocument()
  })

  it("links already-recorded appointments to the web session detail", () => {
    mockSummary([makeAppointment({ status: "completed", session_id: "sess-99" })])

    renderPanel()

    const open = screen.getByRole("link", { name: /^open$/i })
    expect(open).toHaveAttribute("href", "/dashboard/sessions/sess-99")
  })

  it("surfaces last-visit hint when the summary resolves it", () => {
    mockSummary(
      [makeAppointment({ patient_id: "patient-7" })],
      // 4 weeks before the appointment.
      { "patient-7": "2026-04-09T13:00:00Z" },
    )

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
