// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PortalAppointments: every state the module can be in, over a mocked client.
 *
 * The states that matter are the ones about the practice's policy rather than
 * about the data, because those are the ones a wrong guess renders as a
 * button that does nothing:
 *
 *   booking on, a type opened  -> the list plus "Request an appointment"
 *   booking on, no type opened -> the list plus why there is nothing to book
 *   booking off                -> the list plus how to reach the practice
 *   the policy call failed     -> the list, and no booking control on a guess
 *
 * And, running through all of them: no disabled control anywhere. A greyed
 * out button with nothing beside it is the failure this module replaced, so
 * the absence is asserted rather than assumed.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type {
  PatientAppointment,
  PatientBookingOptions,
} from "@/lib/api/patientAppointments"
import * as api from "@/lib/api/patientAppointments"
import { PortalAppointments } from "../PortalAppointments"
import {
  BOOKING_OFF,
  NOTHING_BOOKABLE,
  NO_UPCOMING,
  PENDING_NOTICE,
} from "../appointmentsCopy"

vi.mock("@/lib/api/patientAppointments")

const TOKEN = "session-token"
/** A fixed clock, so "upcoming" and "earlier" are not the wall clock's. */
const NOW = new Date("2026-09-20T12:00:00Z")

const OPTIONS: PatientBookingOptions = {
  self_booking: true,
  session_types: [{ name: "Therapy session", duration_minutes: 50 }],
  min_notice_hours: 24,
  max_horizon_days: 60,
  cancel_cutoff_hours: 24,
  reschedule_cutoff_hours: 24,
  practice_timezone: "America/New_York",
  practice_phone: "(555) 010-2020",
}

const upcoming: PatientAppointment = {
  id: "a1",
  start_at: "2026-09-24T14:00:00Z",
  end_at: "2026-09-24T14:50:00Z",
  duration_minutes: 50,
  status: "confirmed",
  session_type: "Therapy session",
}

const pending: PatientAppointment = {
  ...upcoming,
  id: "a2",
  start_at: "2026-09-26T14:00:00Z",
  end_at: "2026-09-26T14:50:00Z",
  status: "pending",
}

const past: PatientAppointment = {
  ...upcoming,
  id: "a0",
  start_at: "2026-09-10T14:00:00Z",
  end_at: "2026-09-10T14:50:00Z",
  status: "completed",
}

function renderModule() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "PortalAppointmentsWrapper"
  return render(<PortalAppointments sessionToken={TOKEN} now={NOW} />, { wrapper: Wrapper })
}

/** No control anywhere on screen is disabled. */
function expectNothingDisabled() {
  for (const button of screen.queryAllByRole("button")) {
    expect(button, `"${button.textContent ?? ""}" is disabled`).not.toBeDisabled()
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getPatientAppointments).mockResolvedValue({ ok: true, data: [] })
  vi.mocked(api.getBookingOptions).mockResolvedValue({ ok: true, data: OPTIONS })
  vi.mocked(api.getPatientSlots).mockResolvedValue({ ok: true, data: [] })
})

describe("PortalAppointments", () => {
  it("shows the empty state and an invitation to book", async () => {
    renderModule()

    expect(await screen.findByTestId("appointments-none-upcoming")).toHaveTextContent(NO_UPCOMING)
    expect(screen.getByTestId("portal-appointments-book")).toBeInTheDocument()
    expect(screen.queryByTestId("portal-appointments-booking-off")).not.toBeInTheDocument()
    expectNothingDisabled()
  })

  it("splits upcoming from earlier and marks what the practice has not confirmed", async () => {
    vi.mocked(api.getPatientAppointments).mockResolvedValue({
      ok: true,
      data: [past, pending, upcoming],
    })
    renderModule()

    await screen.findByTestId("appointments-list")
    const rows = screen.getAllByTestId("appointments-row")
    // Two upcoming, soonest first, then the earlier one inside its details.
    expect(rows).toHaveLength(3)
    expect(within(rows[0]).getByTestId("appointments-row-status")).toHaveTextContent("confirmed")
    expect(within(rows[1]).getByTestId("appointments-row-pending")).toHaveTextContent(
      PENDING_NOTICE,
    )
    expect(screen.getByTestId("appointments-past")).toBeInTheDocument()
  })

  it("renders times in the practice's timezone, not the browser's", async () => {
    vi.mocked(api.getPatientAppointments).mockResolvedValue({ ok: true, data: [upcoming] })
    renderModule()

    // 14:00 UTC is 10:00 in America/New_York on that date. The assertion is
    // the whole point of carrying the zone: a browser left on UTC would
    // otherwise show 2:00 PM for an appointment the practice made at 10.
    expect(await screen.findByTestId("appointments-row-when")).toHaveTextContent("10:00 AM")
  })

  it("offers no change controls and says how to book when the practice is off", async () => {
    vi.mocked(api.getPatientAppointments).mockResolvedValue({ ok: true, data: [upcoming] })
    vi.mocked(api.getBookingOptions).mockResolvedValue({
      ok: true,
      data: { ...OPTIONS, self_booking: false },
    })
    renderModule()

    const notice = await screen.findByTestId("portal-appointments-booking-off")
    expect(notice).toHaveTextContent(BOOKING_OFF)
    expect(notice).toHaveTextContent("(555) 010-2020")
    // The list is still there, read-only.
    expect(screen.getByTestId("appointments-row")).toBeInTheDocument()
    expect(screen.queryByTestId("appointments-row-reschedule")).not.toBeInTheDocument()
    expect(screen.queryByTestId("appointments-row-cancel")).not.toBeInTheDocument()
    expect(screen.queryByTestId("portal-appointments-book")).not.toBeInTheDocument()
    expectNothingDisabled()
  })

  it("falls back to contacting the practice when it published no number", async () => {
    vi.mocked(api.getBookingOptions).mockResolvedValue({
      ok: true,
      data: { ...OPTIONS, self_booking: false, practice_phone: null },
    })
    renderModule()

    const notice = await screen.findByTestId("portal-appointments-booking-off")
    expect(notice).toHaveTextContent("Contact your practice")
  })

  it("says why there is nothing to book when the practice opened no type", async () => {
    vi.mocked(api.getBookingOptions).mockResolvedValue({
      ok: true,
      data: { ...OPTIONS, session_types: [] },
    })
    renderModule()

    // Not "booking is off" — the practice turned it on. Saying so would be
    // false, and an empty picker would read as a bug.
    expect(await screen.findByTestId("portal-appointments-booking-off")).toHaveTextContent(
      NOTHING_BOOKABLE,
    )
  })

  it("still lists appointments when the policy call fails", async () => {
    vi.mocked(api.getPatientAppointments).mockResolvedValue({ ok: true, data: [upcoming] })
    vi.mocked(api.getBookingOptions).mockResolvedValue({
      ok: false,
      status: 500,
      code: null,
      message: null,
    })
    renderModule()

    // The half that still works keeps working, and nothing is offered on a
    // guess about a policy we could not read.
    expect(await screen.findByTestId("appointments-row")).toBeInTheDocument()
    expect(screen.queryByTestId("portal-appointments-book")).not.toBeInTheDocument()
    expect(screen.getByTestId("portal-appointments-booking-off")).toBeInTheDocument()
  })

  it("reports a failure to load the appointments themselves", async () => {
    vi.mocked(api.getPatientAppointments).mockResolvedValue({
      ok: false,
      status: 503,
      code: null,
      message: null,
    })
    renderModule()

    expect(await screen.findByTestId("portal-appointments-error")).toBeInTheDocument()
  })

  it("offers a join link only inside the join window", async () => {
    const live: PatientAppointment = {
      ...upcoming,
      start_at: "2026-09-20T12:05:00Z",
      end_at: "2026-09-20T12:55:00Z",
      video_link: "https://meet.example.com/abc-defg-hij",
    }
    const later: PatientAppointment = {
      ...upcoming,
      id: "a3",
      video_link: "https://meet.example.com/zzz-zzzz-zzz",
    }
    vi.mocked(api.getPatientAppointments).mockResolvedValue({ ok: true, data: [live, later] })
    renderModule()

    await screen.findByTestId("appointments-list")
    // One of the two is within fifteen minutes of starting; the other is days
    // away and carries a link all the same.
    expect(screen.getAllByTestId("appointments-row-join")).toHaveLength(1)
  })

  it("walks a patient from the list into the picker and back out", async () => {
    const user = userEvent.setup()
    renderModule()

    await user.click(await screen.findByTestId("portal-appointments-book"))
    expect(await screen.findByTestId("appointments-slot-picker")).toBeInTheDocument()
    expect(api.getPatientSlots).toHaveBeenCalledWith(
      expect.objectContaining({ token: TOKEN, durationMinutes: 50 }),
    )

    await user.click(screen.getByTestId("appointments-booking-cancel"))
    expect(await screen.findByTestId("appointments-list")).toBeInTheDocument()
  })

  it("stops offering booking when the practice turns it off mid-journey", async () => {
    const user = userEvent.setup()
    vi.mocked(api.getPatientSlots).mockResolvedValue({
      ok: false,
      status: 403,
      code: "SELF_BOOKING_DISABLED",
      message: "This practice does not offer online booking.",
    })
    renderModule()

    await user.click(await screen.findByTestId("portal-appointments-book"))

    // Back on the list, with the sentence that says what to do instead —
    // rather than an empty picker or a spinner that never resolves.
    expect(await screen.findByTestId("portal-appointments-booking-off")).toBeInTheDocument()
    expect(screen.queryByTestId("portal-appointments-book")).not.toBeInTheDocument()
  })
})
