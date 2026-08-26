// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { EditorialEventPeek } from "../EditorialEventPeek"
import type { AppointmentResponse } from "@/types/scheduling"

function appointment(overrides: Partial<AppointmentResponse> = {}): AppointmentResponse {
  return {
    id: "a1",
    user_id: "u1",
    patient_id: "p1",
    title: "Auto title",
    start_at: "2026-06-01T09:00:00",
    end_at: "2026-06-01T09:50:00",
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
    created_at: "2026-06-01T08:00:00",
    updated_at: null,
    ...overrides,
  }
}

const RECT = {
  top: 100,
  left: 100,
  right: 200,
  bottom: 140,
  width: 100,
  height: 40,
  x: 100,
  y: 100,
  toJSON: () => ({}),
} as DOMRect

function renderPeek(props: Partial<Parameters<typeof EditorialEventPeek>[0]> = {}) {
  return render(
    <EditorialEventPeek
      appointment={appointment()}
      patientName="Jane Doe"
      anchorRect={RECT}
      onClose={vi.fn()}
      onEdit={vi.fn()}
      {...props}
    />,
  )
}

describe("EditorialEventPeek", () => {
  it("renders the full name, status pill, date/time range, and session type + duration", () => {
    renderPeek()
    expect(screen.getByText("Jane Doe")).toBeInTheDocument()
    expect(screen.getByText("Confirmed")).toBeInTheDocument()
    expect(screen.getByText(/Monday, Jun 1/)).toBeInTheDocument()
    expect(screen.getByText(/9:00\s+–\s+9:50\s*AM/)).toBeInTheDocument()
    expect(screen.getByText(/Individual · 50 min/)).toBeInTheDocument()
  })

  it("shows the double-click tip and an Edit button", () => {
    renderPeek()
    expect(screen.getByText(/double-click to edit/i)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument()
  })

  it("renders the video link only when present", () => {
    const { rerender } = renderPeek()
    expect(screen.queryByRole("link")).not.toBeInTheDocument()

    rerender(
      <EditorialEventPeek
        appointment={appointment({ video_link: "https://meet.example/abc" })}
        patientName="Jane Doe"
        anchorRect={RECT}
        onClose={vi.fn()}
        onEdit={vi.fn()}
      />,
    )
    expect(screen.getByRole("link")).toHaveAttribute(
      "href",
      "https://meet.example/abc",
    )
  })

  it("renders a non-http video_link as plain text, not an anchor", () => {
    // Stored javascript:/data: values must not become click-exec vectors.
    render(
      <EditorialEventPeek
        appointment={appointment({ video_link: "javascript:alert(1)" })}
        patientName="Jane Doe"
        anchorRect={RECT}
        onClose={vi.fn()}
        onEdit={vi.fn()}
      />,
    )
    expect(screen.queryByRole("link")).not.toBeInTheDocument()
    expect(screen.getByText("javascript:alert(1)")).toBeInTheDocument()
  })

  it("calls onEdit with the appointment when Edit is clicked", () => {
    const onEdit = vi.fn()
    const appt = appointment()
    render(
      <EditorialEventPeek
        appointment={appt}
        patientName="Jane Doe"
        anchorRect={RECT}
        onClose={vi.fn()}
        onEdit={onEdit}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Edit" }))
    expect(onEdit).toHaveBeenCalledWith(appt)
  })

  it("closes on Escape and on an outside pointerdown", () => {
    const onClose = vi.fn()
    renderPeek({ onClose })

    fireEvent.keyDown(window, { key: "Escape" })
    expect(onClose).toHaveBeenCalledTimes(1)

    fireEvent.pointerDown(document.body)
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it("does not close when a pointerdown lands inside the popover", () => {
    const onClose = vi.fn()
    renderPeek({ onClose })
    fireEvent.pointerDown(screen.getByText("Jane Doe"))
    expect(onClose).not.toHaveBeenCalled()
  })
})
