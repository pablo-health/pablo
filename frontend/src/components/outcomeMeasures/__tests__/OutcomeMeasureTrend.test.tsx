// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * OutcomeMeasureTrend tests.
 *
 * This is the surface a clinician reads to decide whether somebody is getting
 * better, and it had no test. What it must get right is mostly restraint: the
 * backend scores an administration and this renders what came back, so the
 * failure worth catching is the component deciding a number or a severity
 * word for itself. The rest is order — a trend drawn backwards says the
 * opposite of the truth and looks entirely normal.
 *
 * The real instrument metadata is used rather than a stand-in, because the
 * PHQ-9 safety signal is a specific item at a specific threshold and a test
 * that invented its own would not be holding the shipped one to anything.
 */

import { describe, it, expect, vi } from "vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { OutcomeMeasureTrend } from "../OutcomeMeasureTrend"
import { getInstrumentMeta, type InstrumentMeta } from "@/lib/outcomeMeasures"
import type { OutcomeMeasure } from "@/types/outcomeMeasures"

const PHQ9 = getInstrumentMeta("phq9") as InstrumentMeta

/** All nine items answered, item 9 left at zero unless a test says otherwise. */
function itemScores(overrides: Record<string, number> = {}): Record<string, number> {
  const scores: Record<string, number> = {}
  for (let item = 1; item <= 9; item++) scores[String(item)] = 0
  return { ...scores, ...overrides }
}

function measure(overrides: Partial<OutcomeMeasure> = {}): OutcomeMeasure {
  return {
    id: "measure-1",
    patient_id: "patient-a",
    session_id: null,
    appointment_id: null,
    instrument: "phq9",
    total_score: 12,
    item_scores: itemScores(),
    is_complete: true,
    source: "patient_self_report",
    item_citations: null,
    administered_at: "2026-03-01T12:00:00Z",
    created_by: "user-1",
    created_at: "2026-03-01T12:00:00Z",
    updated_at: "2026-03-01T12:00:00Z",
    severity: "moderate",
    ...overrides,
  }
}

function renderTrend(measures: OutcomeMeasure[], onDelete = vi.fn()) {
  render(
    <OutcomeMeasureTrend meta={PHQ9} measures={measures} onDelete={onDelete} />,
  )
  return onDelete
}

/** The sparkline's plotted points, in the order the polyline draws them. */
function plottedTotals(): number[] {
  const line = document.querySelector("polyline")
  if (!line) return []
  const max = PHQ9.items.length * 3
  const H = 40
  const pad = 4
  return (line.getAttribute("points") ?? "")
    .split(" ")
    .filter(Boolean)
    .map((pair) => {
      const y = Number(pair.split(",")[1])
      // Invert the component's own projection back to a score.
      return Math.round((1 - (y - pad) / (H - pad * 2)) * max)
    })
}

describe("OutcomeMeasureTrend", () => {
  it("renders nothing at all when there is no administration", () => {
    const { container } = render(
      <OutcomeMeasureTrend meta={PHQ9} measures={[]} onDelete={vi.fn()} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it("leads with the newest score and the severity the server returned", () => {
    renderTrend([
      measure({ id: "older", total_score: 18, severity: "moderately severe" }),
      measure({
        id: "newest",
        total_score: 4,
        severity: "minimal",
        administered_at: "2026-05-01T12:00:00Z",
      }),
    ])

    // The headline is the LAST administration, not the highest or the first.
    expect(screen.getByTestId("outcome-latest-score")).toHaveTextContent("4")
    expect(screen.getByTestId("outcome-latest-severity")).toHaveTextContent("minimal")
  })

  it("never words a severity the server did not send", () => {
    // A complete administration whose severity the server withheld. The
    // component knows the total and the instrument, so it COULD name a band;
    // scoring lives on the backend and it must not.
    renderTrend([measure({ total_score: 27, severity: null })])

    expect(screen.getByTestId("outcome-latest-score")).toHaveTextContent("27")
    expect(screen.queryByTestId("outcome-latest-severity")).not.toBeInTheDocument()
    for (const band of ["minimal", "mild", "moderate", "moderately severe", "severe"]) {
      expect(screen.queryByText(band)).not.toBeInTheDocument()
    }
  })

  it("plots the administrations oldest to newest, the way they were given", () => {
    renderTrend([
      measure({ id: "a", total_score: 3, administered_at: "2026-01-01T12:00:00Z" }),
      measure({ id: "b", total_score: 15, administered_at: "2026-02-01T12:00:00Z" }),
      measure({ id: "c", total_score: 9, administered_at: "2026-03-01T12:00:00Z" }),
    ])

    // A trend drawn backwards is the difference between recovering and
    // deteriorating, and renders without complaint either way.
    expect(plottedTotals()).toEqual([3, 15, 9])
  })

  it("draws no line from a single point", () => {
    renderTrend([measure()])
    expect(document.querySelector("polyline")).toBeNull()
    // The score itself is still there — one administration is worth reading.
    expect(screen.getByTestId("outcome-latest-score")).toHaveTextContent("12")
  })

  it("leaves an incomplete administration out of the line but on the list", () => {
    renderTrend([
      measure({ id: "a", total_score: 6, administered_at: "2026-01-01T12:00:00Z" }),
      measure({
        id: "partial",
        total_score: null,
        severity: null,
        is_complete: false,
        administered_at: "2026-02-01T12:00:00Z",
      }),
      measure({ id: "c", total_score: 10, administered_at: "2026-03-01T12:00:00Z" }),
    ])

    // Plotting a missing total as zero would draw a recovery that never
    // happened; the row still shows, with nothing where the score goes.
    expect(plottedTotals()).toEqual([6, 10])
    expect(screen.getByText("—")).toBeInTheDocument()
    expect(screen.getByText("3 administrations")).toBeInTheDocument()
  })

  it("flags the administration that endorsed item 9, and only that one", () => {
    renderTrend([
      measure({
        id: "safe",
        administered_at: "2026-01-01T12:00:00Z",
        item_scores: itemScores(),
      }),
      measure({
        id: "endorsed",
        administered_at: "2026-02-01T12:00:00Z",
        item_scores: itemScores({ "9": 1 }),
      }),
    ])

    const flags = screen.getAllByText(`Item ${PHQ9.safetySignal?.itemKey}`)
    expect(flags).toHaveLength(1)
  })

  it("keeps the item-9 flag on an older row after a later score comes back clear", () => {
    renderTrend([
      measure({
        id: "endorsed",
        administered_at: "2026-01-01T12:00:00Z",
        item_scores: itemScores({ "9": 2 }),
      }),
      measure({
        id: "clear-since",
        administered_at: "2026-02-01T12:00:00Z",
        total_score: 2,
        severity: "minimal",
        item_scores: itemScores(),
      }),
    ])

    // The endorsement happened. A flag that disappeared because the next
    // administration was clear would erase it from the record a clinician
    // scrolls back through.
    expect(screen.getAllByText(`Item ${PHQ9.safetySignal?.itemKey}`)).toHaveLength(1)
  })

  it("lists the administrations newest first, and deletes the one that was clicked", async () => {
    const onDelete = renderTrend([
      measure({ id: "older", administered_at: "2026-01-09T12:00:00Z" }),
      measure({ id: "newest", administered_at: "2026-03-14T12:00:00Z" }),
    ])

    const rows = screen.getAllByRole("listitem")
    expect(within(rows[0]).getByText(/Mar 14, 2026/)).toBeInTheDocument()

    await userEvent.click(within(rows[0]).getByRole("button", { name: "Delete score" }))
    expect(onDelete).toHaveBeenCalledTimes(1)
    expect(onDelete.mock.calls[0][0].id).toBe("newest")
  })
})
