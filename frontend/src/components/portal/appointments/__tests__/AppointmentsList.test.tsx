// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The list's own behaviour: cancelling, and what happens when the practice's
 * notice period is in the way.
 *
 * The cancel button stays live right up to the appointment on purpose. A
 * patient who cannot attend must always be able to say so — the alternative
 * is a no-show, which costs the practice the slot AND the warning — so the
 * notice period costs money rather than withholding permission, and the
 * warning arrives from the server in the practice's own terms.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { PatientAppointment } from "@/lib/api/patientAppointments"
import * as api from "@/lib/api/patientAppointments"
import { AppointmentsList } from "../AppointmentsList"

vi.mock("@/lib/api/patientAppointments")

const TOKEN = "session-token"
const NOW = new Date("2026-09-20T12:00:00Z")
const ZONE = "America/New_York"

/** Tomorrow morning — inside a 24-hour notice period. */
const soon: PatientAppointment = {
  id: "a1",
  start_at: "2026-09-21T09:00:00Z",
  end_at: "2026-09-21T09:50:00Z",
  duration_minutes: 50,
  status: "confirmed",
  session_type: "Therapy session",
}

function renderList(props: Partial<React.ComponentProps<typeof AppointmentsList>> = {}) {
  const onReschedule = vi.fn()
  const onCancelled = vi.fn()
  render(
    <AppointmentsList
      token={TOKEN}
      appointments={[soon]}
      timeZone={ZONE}
      canChange
      now={NOW}
      onReschedule={onReschedule}
      onCancelled={onCancelled}
      {...props}
    />,
  )
  return { onReschedule, onCancelled }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.cancelPatientAppointment).mockResolvedValue({
    ok: true,
    data: { ...soon, status: "cancelled" },
  })
})

describe("AppointmentsList", () => {
  it("cancels and tells the parent to refetch", async () => {
    const user = userEvent.setup()
    const { onCancelled } = renderList()

    await user.click(screen.getByTestId("appointments-row-cancel"))

    await vi.waitFor(() => expect(onCancelled).toHaveBeenCalled())
    expect(api.cancelPatientAppointment).toHaveBeenCalledWith({
      token: TOKEN,
      appointmentId: "a1",
      acknowledgeLateChange: false,
    })
  })

  it("offers the cancel button inside the notice period, not a disabled one", () => {
    renderList()

    // The appointment is 21 hours away and the practice asks for 24. The
    // button is live: the warning is the server's to give, and refusing here
    // would leave a no-show as the only option.
    expect(screen.getByTestId("appointments-row-cancel")).not.toBeDisabled()
  })

  it("shows the practice's late-notice warning and cancels on confirmation", async () => {
    const user = userEvent.setup()
    const warning =
      "This is inside the practice's notice period, so its cancellation policy may apply. Confirm to go ahead."
    vi.mocked(api.cancelPatientAppointment)
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        code: "LATE_CHANGE_NOT_ACKNOWLEDGED",
        message: warning,
      })
      .mockResolvedValueOnce({ ok: true, data: { ...soon, status: "cancelled" } })
    const { onCancelled } = renderList()

    await user.click(screen.getByTestId("appointments-row-cancel"))
    expect(await screen.findByTestId("appointments-row-late-change")).toHaveTextContent(warning)

    await user.click(screen.getByTestId("appointments-row-late-change-confirm"))

    await vi.waitFor(() => expect(onCancelled).toHaveBeenCalled())
    expect(api.cancelPatientAppointment).toHaveBeenLastCalledWith(
      expect.objectContaining({ acknowledgeLateChange: true }),
    )
  })

  it("keeps the appointment when the patient declines the warning", async () => {
    const user = userEvent.setup()
    vi.mocked(api.cancelPatientAppointment).mockResolvedValue({
      ok: false,
      status: 409,
      code: "LATE_CHANGE_NOT_ACKNOWLEDGED",
      message: "Confirm to go ahead.",
    })
    const { onCancelled } = renderList()

    await user.click(screen.getByTestId("appointments-row-cancel"))
    await user.click(await screen.findByTestId("appointments-row-late-change-cancel"))

    expect(screen.queryByTestId("appointments-row-late-change")).not.toBeInTheDocument()
    expect(onCancelled).not.toHaveBeenCalled()
    expect(api.cancelPatientAppointment).toHaveBeenCalledTimes(1)
  })

  it("reports a failure without claiming the appointment changed", async () => {
    const user = userEvent.setup()
    vi.mocked(api.cancelPatientAppointment).mockResolvedValue({
      ok: false,
      status: 500,
      code: null,
      message: null,
    })
    const { onCancelled } = renderList()

    await user.click(screen.getByTestId("appointments-row-cancel"))

    expect(await screen.findByTestId("appointments-list-error")).toBeInTheDocument()
    expect(onCancelled).not.toHaveBeenCalled()
  })

  it("hands a reschedule up, because the picker is not the list's to own", async () => {
    const user = userEvent.setup()
    const { onReschedule } = renderList()

    await user.click(screen.getByTestId("appointments-row-reschedule"))

    expect(onReschedule).toHaveBeenCalledWith(soon)
  })

  it("offers nothing to act on when the practice takes changes another way", () => {
    renderList({ canChange: false })

    expect(screen.queryByTestId("appointments-row-cancel")).not.toBeInTheDocument()
    expect(screen.queryByTestId("appointments-row-reschedule")).not.toBeInTheDocument()
  })
})
