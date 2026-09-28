// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * AppointmentsSummary: the appointments tile's line on the portal home
 * screen — the next one, in the practice's timezone, and never "Next" for a
 * time the practice has not confirmed.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { PatientAppointment, PatientBookingOptions } from "@/lib/api/patientAppointments"
import * as api from "@/lib/api/patientAppointments"
import { AppointmentsSummary, appointmentsSummaryLine } from "../AppointmentsSummary"
import { formatWhen } from "../formatting"

vi.mock("@/lib/api/patientAppointments")

const TOKEN = "session-token"
const NOW = new Date("2026-09-20T12:00:00Z")
const ZONE = "America/New_York"

const OPTIONS: PatientBookingOptions = {
  self_booking: false,
  session_types: [],
  min_notice_hours: 24,
  max_horizon_days: 60,
  cancel_cutoff_hours: 24,
  reschedule_cutoff_hours: 24,
  practice_timezone: ZONE,
  join_window_before_minutes: 15,
  practice_phone: null,
}

const confirmed: PatientAppointment = {
  id: "a1",
  start_at: "2026-09-24T14:00:00Z",
  end_at: "2026-09-24T14:50:00Z",
  duration_minutes: 50,
  status: "confirmed",
  session_type: "Therapy session",
}

const later: PatientAppointment = {
  ...confirmed,
  id: "a2",
  start_at: "2026-10-01T14:00:00Z",
  end_at: "2026-10-01T14:50:00Z",
}

const past: PatientAppointment = {
  ...confirmed,
  id: "a0",
  start_at: "2026-09-10T14:00:00Z",
  end_at: "2026-09-10T14:50:00Z",
  status: "completed",
}

function renderSummary() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <AppointmentsSummary sessionToken={TOKEN} now={NOW} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getBookingOptions).mockResolvedValue({ ok: true, data: OPTIONS })
})

describe("appointmentsSummaryLine", () => {
  it("names the soonest upcoming appointment, skipping ones already over", () => {
    expect(appointmentsSummaryLine([later, past, confirmed], ZONE, NOW)).toBe(
      `Next: ${formatWhen(confirmed.start_at, ZONE)}`,
    )
  })

  it("calls an unconfirmed time a request", () => {
    const pending = { ...confirmed, status: "pending" as const }
    expect(appointmentsSummaryLine([pending], ZONE, NOW)).toBe(
      `Requested: ${formatWhen(confirmed.start_at, ZONE)}`,
    )
  })

  it("says so when nothing is coming up", () => {
    expect(appointmentsSummaryLine([past], ZONE, NOW)).toBe("No upcoming appointments")
    expect(appointmentsSummaryLine([], ZONE, NOW)).toBe("No upcoming appointments")
  })
})

describe("AppointmentsSummary", () => {
  it("renders the next appointment in the practice's timezone", async () => {
    vi.mocked(api.getPatientAppointments).mockResolvedValue({ ok: true, data: [confirmed] })

    renderSummary()

    expect(await screen.findByText(`Next: ${formatWhen(confirmed.start_at, ZONE)}`)).toBeTruthy()
  })

  it("renders nothing rather than guess a timezone it could not read", async () => {
    vi.mocked(api.getPatientAppointments).mockResolvedValue({ ok: true, data: [confirmed] })
    vi.mocked(api.getBookingOptions).mockResolvedValue({
      ok: false,
      status: 500,
      code: null,
      message: null,
    })

    const { container } = renderSummary()

    await waitFor(() => expect(api.getBookingOptions).toHaveBeenCalled())
    await waitFor(() => expect(api.getPatientAppointments).toHaveBeenCalled())
    expect(container.textContent).toBe("")
  })

  it("renders nothing when the list fails to load", async () => {
    vi.mocked(api.getPatientAppointments).mockResolvedValue({
      ok: false,
      status: 500,
      code: null,
      message: null,
    })

    const { container } = renderSummary()

    await waitFor(() => expect(api.getPatientAppointments).toHaveBeenCalled())
    expect(container.textContent).toBe("")
  })
})
