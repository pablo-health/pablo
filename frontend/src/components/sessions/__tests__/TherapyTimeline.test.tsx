// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * TherapyTimeline: the client-present span as labeled runs; a boundary drags
 * (or steps with the arrow keys) one turn at a time.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"

import { TherapyTimeline } from "../TherapyTimeline"
import type { Labels } from "@/lib/therapyTimeline"
import type { RecordingTurn } from "@/types/visitTimes"

const TZ = "America/New_York"
// 10:00 AM in New York.
const STARTED = "2026-10-06T14:00:00Z"

// Five ten-minute turns; the client left at 50:00.
const TURNS: RecordingTurn[] = [0, 10, 20, 30, 40].map((m) => ({
  seconds: m * 60,
  end_seconds: (m + 10) * 60,
  speaker: "Therapist",
  text: "",
  label: null,
}))
const LABELS: Labels = ["medication_management", "therapy", "therapy", "therapy", "admin"]

function renderTimeline(onChange = vi.fn()) {
  render(
    <TherapyTimeline
      turns={TURNS}
      labels={LABELS}
      onChange={onChange}
      endSeconds={50 * 60}
      startedAt={STARTED}
      timeZone={TZ}
    />,
  )
  return onChange
}

describe("TherapyTimeline", () => {
  const captured = new Set<number>()

  beforeEach(() => {
    // jsdom has no layout and no pointer capture: a 500px-wide bar, and capture by hand.
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0,
      width: 500,
    } as DOMRect)
    HTMLElement.prototype.setPointerCapture = (id: number) => void captured.add(id)
    HTMLElement.prototype.hasPointerCapture = (id: number) => captured.has(id)
  })
  afterEach(() => {
    captured.clear()
    vi.restoreAllMocks()
  })

  it("draws one run per label, in order, across the span", () => {
    renderTimeline()

    const runs = screen.getAllByTestId("timeline-run")
    expect(runs.map((r) => r.getAttribute("data-label"))).toEqual([
      "medication_management",
      "therapy",
      "admin",
    ])
    expect(runs[1]).toHaveAccessibleName("Therapy, 10:10 AM to 10:40 AM")
    expect(runs[1]).toHaveStyle({ flexBasis: "60%" })
  })

  it("drags a boundary to the turn nearest the pointer", () => {
    const onChange = renderTimeline()

    // The boundary between the medication check and the therapy, dragged to 20:00 (200px).
    const boundary = screen.getByRole("slider", { name: "Boundary at 10:10 AM" })
    fireEvent.pointerDown(boundary, { pointerId: 1, clientX: 100 })
    fireEvent.pointerMove(boundary, { pointerId: 1, clientX: 205 })

    expect(onChange).toHaveBeenCalledWith([
      "medication_management",
      "medication_management",
      "therapy",
      "therapy",
      "admin",
    ])
  })

  it("never drags a boundary past the runs beside it", () => {
    const onChange = renderTimeline()

    const boundary = screen.getByRole("slider", { name: "Boundary at 10:40 AM" })
    fireEvent.pointerDown(boundary, { pointerId: 2, clientX: 400 })
    fireEvent.pointerMove(boundary, { pointerId: 2, clientX: 0 })

    // As far as the therapy run's second turn: the run keeps its first.
    expect(onChange).toHaveBeenCalledWith([
      "medication_management",
      "therapy",
      "admin",
      "admin",
      "admin",
    ])
  })

  it("does nothing on a pointer it did not capture", () => {
    const onChange = renderTimeline()

    fireEvent.pointerMove(screen.getByRole("slider", { name: "Boundary at 10:10 AM" }), {
      pointerId: 3,
      clientX: 300,
    })

    expect(onChange).not.toHaveBeenCalled()
  })
})
