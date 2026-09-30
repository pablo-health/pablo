// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { GoogleChangeNotice } from "../GoogleChangeNotice"
import { GoogleChangesBanner } from "../GoogleChangesBanner"
import { EditorialEventCard } from "../EditorialEventCard"
import { EditorialEventPeek } from "../EditorialEventPeek"
import type { AppointmentResponse } from "@/types/scheduling"

function appointment(overrides: Partial<AppointmentResponse> = {}): AppointmentResponse {
  return {
    id: "a1",
    user_id: "u1",
    patient_id: "p1",
    title: "Auto title",
    start_at: "2030-06-03T09:00:00",
    end_at: "2030-06-03T09:50:00",
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
    google_event_id: "evt-1",
    google_sync_status: "synced",
    session_id: null,
    created_at: "2030-06-01T08:00:00",
    updated_at: null,
    ...overrides,
  }
}

const moved = () => appointment({ google_sync_status: "external_change" })
const removed = () =>
  appointment({ status: "cancelled", google_sync_status: "removed_in_google" })

describe("GoogleChangeNotice", () => {
  it("explains a move it couldn't follow and offers both times", () => {
    const onResolve = vi.fn()
    render(<GoogleChangeNotice appointment={moved()} onResolve={onResolve} />)

    expect(screen.getByText(/Google Calendar has a different time/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Keep this time" }))
    fireEvent.click(screen.getByRole("button", { name: "Use Google's time" }))

    expect(onResolve.mock.calls.map((c) => c[1])).toEqual(["keep_pablo", "accept_google"])
  })

  it("offers Undo on a session cancelled because it was removed in Google", () => {
    const onResolve = vi.fn()
    render(<GoogleChangeNotice appointment={removed()} onResolve={onResolve} />)

    expect(
      screen.getByText("Removed from Google Calendar, so cancelled here."),
    ).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Undo" }))

    expect(onResolve).toHaveBeenCalledWith(expect.objectContaining({ id: "a1" }), "keep_pablo")
  })

  it("renders nothing for a session Google agrees with", () => {
    const { container } = render(
      <GoogleChangeNotice appointment={appointment()} onResolve={vi.fn()} />,
    )
    expect(container).toBeEmptyDOMElement()
  })
})

describe("the calendar marks a move it couldn't follow", () => {
  it("shows a marker on the event and says why", () => {
    render(<EditorialEventCard appointment={moved()} patientName="Jane Doe" micro />)

    expect(screen.getByTestId("google-change-marker")).toBeInTheDocument()
    expect(screen.getByRole("button")).toHaveAccessibleName(
      /Google Calendar has a different time/,
    )
  })

  it("shows no marker on an ordinary event", () => {
    render(<EditorialEventCard appointment={appointment()} patientName="Jane Doe" />)
    expect(screen.queryByTestId("google-change-marker")).not.toBeInTheDocument()
  })

  it("puts the choice in the event's peek", () => {
    const onResolve = vi.fn()
    render(
      <EditorialEventPeek
        appointment={moved()}
        patientName="Jane Doe"
        anchorRect={new DOMRect(100, 100, 100, 40)}
        onClose={vi.fn()}
        onEdit={vi.fn()}
        onResolveGoogleChange={onResolve}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Keep this time" }))
    expect(onResolve).toHaveBeenCalledWith(expect.objectContaining({ id: "a1" }), "keep_pablo")
  })
})

describe("GoogleChangesBanner", () => {
  const NOW = new Date("2030-06-01T12:00:00")
  const names = new Map([["p1", "Jane Doe"]])

  it("lists an upcoming session removed in Google with Undo, even though cancelled ones are hidden", () => {
    const onResolve = vi.fn()
    render(
      <GoogleChangesBanner
        appointments={[removed(), appointment({ id: "a2" })]}
        patientMap={names}
        heldCount={0}
        onResolve={onResolve}
        onResolveHeld={vi.fn()}
        now={NOW}
      />,
    )

    const lines = screen.getAllByTestId("google-removed-line")
    expect(lines).toHaveLength(1)
    expect(lines[0]).toHaveTextContent(
      "Jane Doe, Mon Jun 3, 9:00 AM: removed from Google Calendar, so cancelled here.",
    )
    fireEvent.click(screen.getByRole("button", { name: "Undo" }))
    expect(onResolve).toHaveBeenCalledWith(expect.objectContaining({ id: "a1" }), "keep_pablo")
  })

  it("asks once about a bulk removal", () => {
    const onResolveHeld = vi.fn()
    render(
      <GoogleChangesBanner
        appointments={[]}
        patientMap={names}
        heldCount={20}
        onResolve={vi.fn()}
        onResolveHeld={onResolveHeld}
        now={NOW}
      />,
    )

    expect(screen.getAllByTestId("google-held-removals")).toHaveLength(1)
    expect(
      screen.getByText(
        "20 sessions were removed from Google Calendar at once, so they're still booked here.",
      ),
    ).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Put back in Google Calendar" }))
    fireEvent.click(screen.getByRole("button", { name: "Cancel here too" }))
    expect(onResolveHeld.mock.calls).toEqual([["keep_pablo"], ["accept_google"]])
  })

  it("says nothing when nothing is waiting, including removals already past", () => {
    const { container } = render(
      <GoogleChangesBanner
        appointments={[removed(), appointment({ id: "a2" })]}
        patientMap={names}
        heldCount={0}
        onResolve={vi.fn()}
        onResolveHeld={vi.fn()}
        now={new Date("2030-07-01T12:00:00")}
      />,
    )
    expect(container).toBeEmptyDOMElement()
  })
})
