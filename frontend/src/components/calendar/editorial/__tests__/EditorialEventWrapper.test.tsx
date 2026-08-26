// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, act } from "@testing-library/react"
import { EditorialEventWrapper } from "../EditorialEventWrapper"
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

describe("EditorialEventWrapper", () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it("opens the peek (not edit) on a single click after the disambiguation delay", () => {
    const onPeek = vi.fn()
    const onEdit = vi.fn()
    const appt = appointment()
    render(
      <EditorialEventWrapper appointment={appt} onPeek={onPeek} onEdit={onEdit}>
        <div>Card</div>
      </EditorialEventWrapper>,
    )

    fireEvent.click(screen.getByText("Card"))
    // Before the timer elapses, neither has fired.
    expect(onPeek).not.toHaveBeenCalled()
    expect(onEdit).not.toHaveBeenCalled()

    act(() => {
      vi.advanceTimersByTime(220)
    })
    expect(onPeek).toHaveBeenCalledTimes(1)
    expect(onPeek.mock.calls[0][0]).toBe(appt)
    expect(onEdit).not.toHaveBeenCalled()
  })

  it("opens edit (not peek) on a double click and cancels the pending single-click peek", () => {
    const onPeek = vi.fn()
    const onEdit = vi.fn()
    const appt = appointment()
    render(
      <EditorialEventWrapper appointment={appt} onPeek={onPeek} onEdit={onEdit}>
        <div>Card</div>
      </EditorialEventWrapper>,
    )

    const card = screen.getByText("Card")
    // A real double-click fires click, click, then dblclick.
    fireEvent.click(card)
    fireEvent.click(card)
    fireEvent.doubleClick(card)

    act(() => {
      vi.advanceTimersByTime(500)
    })
    expect(onEdit).toHaveBeenCalledTimes(1)
    expect(onEdit).toHaveBeenCalledWith(appt)
    // The deferred peek must have been cancelled by the double-click.
    expect(onPeek).not.toHaveBeenCalled()
  })

  it("passes the event's bounding rect to onPeek for anchoring", () => {
    const onPeek = vi.fn()
    const appt = appointment()
    render(
      <EditorialEventWrapper appointment={appt} onPeek={onPeek} onEdit={vi.fn()}>
        <div>Card</div>
      </EditorialEventWrapper>,
    )
    fireEvent.click(screen.getByText("Card"))
    act(() => {
      vi.advanceTimersByTime(220)
    })
    // jsdom returns a zeroed DOMRect, but it must be a defined rect object.
    expect(onPeek.mock.calls[0][1]).toBeDefined()
    expect(typeof onPeek.mock.calls[0][1].top).toBe("number")
  })

  it("opens the peek on Enter keydown (keyboard accessibility)", () => {
    const onPeek = vi.fn()
    const appt = appointment()
    render(
      <EditorialEventWrapper appointment={appt} onPeek={onPeek} onEdit={vi.fn()}>
        <div>Card</div>
      </EditorialEventWrapper>,
    )
    // The wrapper should be a focusable role=button element.
    const wrapper = screen.getByRole("button")
    fireEvent.keyDown(wrapper, { key: "Enter" })
    expect(onPeek).toHaveBeenCalledTimes(1)
    expect(onPeek.mock.calls[0][0]).toBe(appt)
  })

  it("opens the peek on Space keydown (keyboard accessibility)", () => {
    const onPeek = vi.fn()
    const appt = appointment()
    render(
      <EditorialEventWrapper appointment={appt} onPeek={onPeek} onEdit={vi.fn()}>
        <div>Card</div>
      </EditorialEventWrapper>,
    )
    const wrapper = screen.getByRole("button")
    fireEvent.keyDown(wrapper, { key: " " })
    expect(onPeek).toHaveBeenCalledTimes(1)
  })
})
