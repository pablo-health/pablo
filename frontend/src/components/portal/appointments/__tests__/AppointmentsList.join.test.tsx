// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * When a patient is offered a way into the room, and what they read until then.
 *
 * The window is the practice's, not this component's: a deployment that opens
 * its rooms an hour early must open them an hour early here too, and a
 * component holding its own constant is how the two stop agreeing.
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import type { PatientAppointment } from "@/lib/api/patientAppointments"
import { AppointmentsList } from "../AppointmentsList"
import { JOIN, JOIN_LATER } from "../appointmentsCopy"

vi.mock("@/lib/api/patientAppointments")

const TOKEN = "session-token"
const ZONE = "America/New_York"
const START = "2026-09-21T09:00:00Z"
const END = "2026-09-21T09:50:00Z"

function anAppointment(overrides: Partial<PatientAppointment> = {}): PatientAppointment {
  return {
    id: "a1",
    start_at: START,
    end_at: END,
    duration_minutes: 50,
    status: "confirmed",
    session_type: "Therapy session",
    ...overrides,
  }
}

function renderAt(
  now: string,
  appointment: PatientAppointment,
  joinWindowMinutes?: number,
) {
  render(
    <AppointmentsList
      token={TOKEN}
      appointments={[appointment]}
      timeZone={ZONE}
      canChange
      joinWindowMinutes={joinWindowMinutes}
      now={new Date(now)}
      onReschedule={vi.fn()}
      onCancelled={vi.fn()}
    />,
  )
}

const withLink = anAppointment({ video_link: "https://z.test/81", provider: "zoom" })

describe("the join button", () => {
  it("is not offered before the window opens", () => {
    renderAt("2026-09-21T08:30:00Z", withLink, 15)
    expect(screen.queryByTestId("appointments-row-join")).not.toBeInTheDocument()
  })

  it("is offered once the window opens", () => {
    renderAt("2026-09-21T08:45:00Z", withLink, 15)
    expect(screen.getByTestId("appointments-row-join")).toHaveTextContent(JOIN)
  })

  it("stays offered until the scheduled end", () => {
    renderAt("2026-09-21T09:49:00Z", withLink, 15)
    expect(screen.getByTestId("appointments-row-join")).toBeInTheDocument()
  })

  it("opens the room in a new tab", () => {
    renderAt("2026-09-21T08:50:00Z", withLink, 15)
    const link = screen.getByTestId("appointments-row-join")
    expect(link).toHaveAttribute("href", "https://z.test/81")
    expect(link).toHaveAttribute("target", "_blank")
  })

  it("follows the practice's window rather than a constant of its own", () => {
    renderAt("2026-09-21T08:15:00Z", withLink, 15)
    expect(screen.queryByTestId("appointments-row-join")).not.toBeInTheDocument()

    render(
      <AppointmentsList
        token={TOKEN}
        appointments={[withLink]}
        timeZone={ZONE}
        canChange
        joinWindowMinutes={60}
        now={new Date("2026-09-21T08:15:00Z")}
        onReschedule={vi.fn()}
        onCancelled={vi.fn()}
      />,
    )
    expect(screen.getAllByTestId("appointments-row-join")).toHaveLength(1)
  })

  it("is not offered on an appointment with no room", () => {
    renderAt("2026-09-21T08:50:00Z", anAppointment(), 15)
    expect(screen.queryByTestId("appointments-row-join")).not.toBeInTheDocument()
  })
})

describe("what a patient reads before the window opens", () => {
  it("says the link is coming", () => {
    renderAt("2026-09-21T08:30:00Z", withLink, 15)
    expect(screen.getByTestId("appointments-row-join-later")).toHaveTextContent(JOIN_LATER)
  })

  it("says it on a video appointment whose link has not arrived yet", () => {
    renderAt("2026-09-21T08:30:00Z", anAppointment({ provider: "google_meet" }), 15)
    expect(screen.getByTestId("appointments-row-join-later")).toBeInTheDocument()
  })

  it("says nothing of the sort about an appointment held in person", () => {
    renderAt("2026-09-21T08:30:00Z", anAppointment(), 15)
    expect(screen.queryByTestId("appointments-row-join-later")).not.toBeInTheDocument()
  })

  it("gives way to the button once the window opens", () => {
    renderAt("2026-09-21T08:50:00Z", withLink, 15)
    expect(screen.queryByTestId("appointments-row-join-later")).not.toBeInTheDocument()
    expect(screen.getByTestId("appointments-row-join")).toBeInTheDocument()
  })

  it("does not appear against an appointment that is already over", () => {
    renderAt("2026-09-22T09:00:00Z", withLink, 15)
    expect(screen.queryByTestId("appointments-row-join-later")).not.toBeInTheDocument()
  })
})
