// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import { addDays, format, startOfDay } from "date-fns"
import type { FloatingBlock } from "@/types/availability"
import { FloatingBlockLayer, type FloatingBlockActions } from "../FloatingBlockBand"

/** The test's own zone, so the practice's clock and the grid's agree. */
const ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone
const ROW_PX = 60
const DAY_START = 7

// Far enough out that it is never today, which has its own label.
const DAY = addDays(startOfDay(new Date()), 10)
const DATE = format(DAY, "yyyy-MM-dd")

function at(hours: number, minutes = 0, day: Date = DAY): string {
  const when = new Date(day)
  when.setHours(hours, minutes, 0, 0)
  return when.toISOString()
}

function block(overrides: Partial<FloatingBlock> = {}): FloatingBlock {
  return {
    rule_id: "rule-lunch",
    label: "Lunch",
    date: DATE,
    state: "floating",
    duration_minutes: 60,
    window_start: "11:30",
    window_end: "14:00",
    start: at(12, 15),
    end: at(13, 15),
    ...overrides,
  }
}

function actions() {
  return {
    onSkip: vi.fn<FloatingBlockActions["onSkip"]>(),
    onPutBack: vi.fn<FloatingBlockActions["onPutBack"]>(),
    onMoveTo: vi.fn<FloatingBlockActions["onMoveTo"]>(),
  }
}

function renderLayer(
  blocks: FloatingBlock[],
  acts = actions(),
  day: Date = DAY,
  onColumnClick = vi.fn(),
) {
  render(
    <div onClick={onColumnClick}>
      <FloatingBlockLayer
        day={day}
        blocks={blocks}
        dayStart={DAY_START}
        rowHeightPx={ROW_PX}
        timeZone={ZONE}
        actions={acts}
      />
    </div>,
  )
  return { acts, onColumnClick }
}

describe("FloatingBlockLayer", () => {
  it("draws a floating block as a band at its start, labelled", () => {
    renderLayer([block()])
    const band = screen.getByTestId("floating-block")
    expect(band).toHaveTextContent("Lunch")
    expect(band).toHaveTextContent("12:15 – 1:15 PM")
    expect(band).toHaveAccessibleName("Lunch, 12:15 PM to 1:15 PM")
    // 12:15 is 5h15m below a 7 AM grid start.
    expect(band.style.top).toBe(`${5.25 * ROW_PX}px`)
    expect(band.style.height).toBe(`${ROW_PX - 2}px`)
  })

  it("draws a pinned block the same way", () => {
    renderLayer([block({ state: "pinned", start: at(13), end: at(14) })])
    expect(screen.getByTestId("floating-block")).toHaveAttribute("data-state", "pinned")
  })

  it("draws no band for a skipped block, only a way to put it back", () => {
    const { acts } = renderLayer([block({ state: "skipped", start: null, end: null })])
    expect(screen.queryByTestId("floating-block")).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Put back Lunch" }))
    expect(acts.onPutBack).toHaveBeenCalledWith(expect.objectContaining({ rule_id: "rule-lunch" }))
  })

  it("draws nothing for an unplaced block", () => {
    renderLayer([block({ state: "unplaced", start: null, end: null })])
    expect(screen.queryByTestId("floating-block")).not.toBeInTheDocument()
    expect(screen.queryByTestId("floating-block-skipped")).not.toBeInTheDocument()
  })

  it("leaves another day's block to that day's column", () => {
    renderLayer([block({ start: at(12, 15, addDays(DAY, 1)), end: at(13, 15, addDays(DAY, 1)) })])
    expect(screen.queryByTestId("floating-block")).not.toBeInTheDocument()
  })

  it("opens its menu without booking the time underneath", () => {
    const { onColumnClick } = renderLayer([block()])
    fireEvent.click(screen.getByTestId("floating-block"))
    expect(screen.getByRole("dialog", { name: "Lunch" })).toHaveTextContent(
      "Between 11:30 AM and 2:00 PM",
    )
    expect(onColumnClick).not.toHaveBeenCalled()
  })

  it("skips the block for its date", () => {
    const { acts } = renderLayer([block()])
    fireEvent.click(screen.getByTestId("floating-block"))
    fireEvent.click(screen.getByRole("button", { name: "Skip this day" }))
    expect(acts.onSkip).toHaveBeenCalledWith(expect.objectContaining({ date: DATE }))
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("says today when the block is today's", () => {
    const today = startOfDay(new Date())
    renderLayer(
      [block({ date: format(today, "yyyy-MM-dd"), start: at(23, 0, today), end: at(23, 30, today) })],
      actions(),
      today,
    )
    fireEvent.click(screen.getByTestId("floating-block"))
    expect(screen.getByRole("button", { name: "Skip today" })).toBeInTheDocument()
  })

  it("offers to put back a pinned block, not a floating one", () => {
    renderLayer([block()])
    fireEvent.click(screen.getByTestId("floating-block"))
    expect(screen.queryByRole("button", { name: "Put back" })).not.toBeInTheDocument()
  })

  it("puts a pinned block back", () => {
    const { acts } = renderLayer([block({ state: "pinned" })])
    fireEvent.click(screen.getByTestId("floating-block"))
    expect(screen.getByRole("dialog")).toHaveTextContent("Set for this day")
    fireEvent.click(screen.getByRole("button", { name: "Put back" }))
    expect(acts.onPutBack).toHaveBeenCalledWith(expect.objectContaining({ state: "pinned" }))
  })

  it("moves the block to a time on the practice's clock", () => {
    const { acts } = renderLayer([block()])
    fireEvent.click(screen.getByTestId("floating-block"))
    const field = screen.getByLabelText("Move to")
    expect(field).toHaveValue("12:15")
    fireEvent.change(field, { target: { value: "13:30" } })
    fireEvent.click(screen.getByRole("button", { name: "Move" }))
    expect(acts.onMoveTo).toHaveBeenCalledWith(
      expect.objectContaining({ rule_id: "rule-lunch" }),
      "13:30",
    )
  })

  it("closes its menu on Escape", () => {
    renderLayer([block()])
    fireEvent.click(screen.getByTestId("floating-block"))
    fireEvent.keyDown(window, { key: "Escape" })
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("draws a past date's band without a menu", () => {
    const past = addDays(startOfDay(new Date()), -3)
    renderLayer(
      [block({ date: format(past, "yyyy-MM-dd"), start: at(12, 0, past), end: at(13, 0, past) })],
      actions(),
      past,
    )
    const band = screen.getByTestId("floating-block")
    expect(band.tagName).toBe("DIV")
    fireEvent.click(band)
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("offers no put-back for a past skipped date", () => {
    const past = addDays(startOfDay(new Date()), -3)
    renderLayer(
      [block({ date: format(past, "yyyy-MM-dd"), state: "skipped", start: null, end: null })],
      actions(),
      past,
    )
    expect(screen.queryByTestId("floating-block-skipped")).not.toBeInTheDocument()
  })

  it("holds a press while a write is in flight", () => {
    const acts = { ...actions(), pending: true }
    renderLayer([block()], acts)
    fireEvent.click(screen.getByTestId("floating-block"))
    fireEvent.click(screen.getByRole("button", { name: "Skip this day" }))
    expect(acts.onSkip).not.toHaveBeenCalled()
  })
})
