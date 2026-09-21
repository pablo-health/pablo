// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The booking journey: browse, confirm, and the three ways it does not go
 * straight through.
 *
 * The confirmation screen reads the status the server sent rather than a prop
 * about how the practice is configured, so both landings are tested against
 * the same flow with only the response changed — which is exactly the
 * distinction a screen driven by a local belief would get wrong.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { PatientAppointment, PatientSlot } from "@/lib/api/patientAppointments"
import * as api from "@/lib/api/patientAppointments"
import { BookingFlow } from "../BookingFlow"
import {
  BOOKED_HEADLINE,
  REQUEST_SENT_HEADLINE,
  RESCHEDULED_HEADLINE,
  SLOT_TAKEN,
} from "../appointmentsCopy"

vi.mock("@/lib/api/patientAppointments")

type SlotsResult = Awaited<ReturnType<typeof api.getPatientSlots>>

const TOKEN = "session-token"
const NOW = new Date("2026-09-20T12:00:00Z")
const ZONE = "America/New_York"

const slot: PatientSlot = {
  start_at: "2026-09-24T14:00:00Z",
  end_at: "2026-09-24T14:50:00Z",
  duration_minutes: 50,
}

const booked: PatientAppointment = {
  id: "a1",
  start_at: slot.start_at,
  end_at: slot.end_at,
  duration_minutes: 50,
  status: "confirmed",
  session_type: "Therapy session",
}

function renderFlow(props: Partial<React.ComponentProps<typeof BookingFlow>> = {}) {
  const onDone = vi.fn()
  const onBookingClosed = vi.fn()
  render(
    <BookingFlow
      token={TOKEN}
      timeZone={ZONE}
      durationMinutes={50}
      maxHorizonDays={60}
      sessionType="Therapy session"
      now={NOW}
      onDone={onDone}
      onBookingClosed={onBookingClosed}
      {...props}
    />,
  )
  return { onDone, onBookingClosed }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getPatientSlots).mockResolvedValue({ ok: true, data: [slot] })
  vi.mocked(api.bookPatientAppointment).mockResolvedValue({ ok: true, data: booked })
  vi.mocked(api.reschedulePatientAppointment).mockResolvedValue({ ok: true, data: booked })
})

describe("BookingFlow", () => {
  it("books a time and lands on a confirmation", async () => {
    const user = userEvent.setup()
    renderFlow()

    await user.click(await screen.findByTestId("appointments-slot"))
    expect(screen.getByTestId("appointments-confirm-when")).toHaveTextContent("10:00 AM")

    await user.click(screen.getByTestId("appointments-confirm-submit"))

    expect(await screen.findByTestId("appointments-confirmation-headline")).toHaveTextContent(
      BOOKED_HEADLINE,
    )
    expect(api.bookPatientAppointment).toHaveBeenCalledWith({
      token: TOKEN,
      startAt: slot.start_at,
      sessionType: "Therapy session",
      durationMinutes: 50,
    })
  })

  it("says a request was sent when the practice reviews bookings", async () => {
    const user = userEvent.setup()
    vi.mocked(api.bookPatientAppointment).mockResolvedValue({
      ok: true,
      data: { ...booked, status: "pending" },
    })
    renderFlow()

    await user.click(await screen.findByTestId("appointments-slot"))
    await user.click(screen.getByTestId("appointments-confirm-submit"))

    // The same journey, the same click: only the server's answer differs.
    expect(await screen.findByTestId("appointments-confirmation-headline")).toHaveTextContent(
      REQUEST_SENT_HEADLINE,
    )
  })

  it("sends the patient back to fresh openings when the time is taken", async () => {
    const user = userEvent.setup()
    vi.mocked(api.bookPatientAppointment).mockResolvedValue({
      ok: false,
      status: 409,
      code: "SLOT_TAKEN",
      message: "That time is no longer available.",
    })
    renderFlow()

    await user.click(await screen.findByTestId("appointments-slot"))
    await user.click(screen.getByTestId("appointments-confirm-submit"))

    expect(await screen.findByTestId("appointments-slot-taken")).toHaveTextContent(SLOT_TAKEN)
    // Refetched rather than redrawn from the list that was already stale.
    expect(api.getPatientSlots).toHaveBeenCalledTimes(2)
  })

  it("reschedules with the practice's own words about late notice", async () => {
    const user = userEvent.setup()
    const warning =
      "This is inside the practice's notice period, so its cancellation policy may apply. Confirm to go ahead."
    vi.mocked(api.reschedulePatientAppointment)
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        code: "LATE_CHANGE_NOT_ACKNOWLEDGED",
        message: warning,
      })
      .mockResolvedValueOnce({ ok: true, data: booked })

    renderFlow({ rescheduling: { ...booked, id: "a9" } })

    await user.click(await screen.findByTestId("appointments-slot"))
    await user.click(screen.getByTestId("appointments-confirm-submit"))

    // The server's sentence, shown verbatim. A client that reworded it would
    // be paraphrasing a policy it has not read.
    expect(await screen.findByTestId("appointments-late-change")).toHaveTextContent(warning)
    expect(api.reschedulePatientAppointment).toHaveBeenLastCalledWith(
      expect.objectContaining({ acknowledgeLateChange: false }),
    )

    await user.click(screen.getByTestId("appointments-late-change-confirm"))

    expect(await screen.findByTestId("appointments-confirmation-headline")).toHaveTextContent(
      RESCHEDULED_HEADLINE,
    )
    expect(api.reschedulePatientAppointment).toHaveBeenLastCalledWith(
      expect.objectContaining({ appointmentId: "a9", acknowledgeLateChange: true }),
    )
  })

  it("hands a mid-journey refusal up rather than drawing its own", async () => {
    const user = userEvent.setup()
    vi.mocked(api.bookPatientAppointment).mockResolvedValue({
      ok: false,
      status: 403,
      code: "SELF_BOOKING_DISABLED",
      message: "This practice does not offer online booking.",
    })
    const { onBookingClosed } = renderFlow()

    await user.click(await screen.findByTestId("appointments-slot"))
    await user.click(screen.getByTestId("appointments-confirm-submit"))

    // One screen answers "what can I do instead", and it is not this one.
    await vi.waitFor(() => expect(onBookingClosed).toHaveBeenCalled())
  })

  it("says nothing is open rather than showing an empty grid", async () => {
    vi.mocked(api.getPatientSlots).mockResolvedValue({ ok: true, data: [] })
    renderFlow()

    expect(await screen.findByTestId("appointments-slots-empty")).toBeInTheDocument()
  })

  it("stops the day arrows at the practice's own horizon", async () => {
    renderFlow({ maxHorizonDays: 1 })

    await screen.findByTestId("appointments-slot-picker")
    // On the first day there is nowhere earlier to go.
    expect(screen.getByTestId("appointments-slots-prev")).toBeDisabled()

    const user = userEvent.setup()
    await user.click(screen.getByTestId("appointments-slots-next"))
    // And one day out is the edge the server enforces too.
    expect(screen.getByTestId("appointments-slots-next")).toBeDisabled()
    expect(screen.getByTestId("appointments-slots-prev")).not.toBeDisabled()
  })

  it("stops offering a day the moment the picker leaves it", async () => {
    const user = userEvent.setup()
    // The first day answers; the second is held in flight, which is when a
    // grid left on screen would be offering times from the day just left.
    let answerSecondDay: (result: SlotsResult) => void = () => {}
    vi.mocked(api.getPatientSlots)
      .mockResolvedValueOnce({ ok: true, data: [slot] })
      .mockImplementationOnce(
        () =>
          new Promise<SlotsResult>((resolve) => {
            answerSecondDay = resolve
          }),
      )
    renderFlow()

    await screen.findByTestId("appointments-slot")
    await user.click(screen.getByTestId("appointments-slots-next"))

    // The heading has moved, so nothing under it still belongs to the day left.
    expect(screen.getByTestId("appointments-slots-day")).toHaveTextContent("September 21")
    expect(screen.queryByTestId("appointments-slot")).not.toBeInTheDocument()
    expect(screen.getByTestId("appointments-slots-loading")).toBeInTheDocument()

    answerSecondDay({ ok: true, data: [] })
    expect(await screen.findByTestId("appointments-slots-empty")).toBeInTheDocument()
  })
})
