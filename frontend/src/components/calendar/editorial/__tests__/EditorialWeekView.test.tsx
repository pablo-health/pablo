// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { EditorialWeekView } from "../EditorialWeekView"
import type { AvailabilityRule } from "@/types/availability"
import type { AppointmentResponse } from "@/types/scheduling"

// Friday October 2 2026, 7 PM: setup finished on a Friday evening, in a week
// that runs Sunday September 27 to Saturday October 3.
const FRIDAY_EVENING = new Date(2026, 9, 2, 19, 0)

function rule(overrides: Partial<AvailabilityRule> = {}): AvailabilityRule {
  return {
    id: "r1",
    user_id: "u1",
    rule_type: "working_hours",
    enforcement: "hard",
    params: {},
    created_at: null,
    updated_at: null,
    ...overrides,
  }
}

/** "9 to 5, Monday to Thursday" — the rules the hours step saves for it. */
const MON_TO_THU_9_TO_5 = [0, 1, 2, 3].map((day) =>
  rule({ id: `wh-${day}`, params: { day_of_week: day, start: "09:00", end: "17:00" } }),
)

function baseProps(availabilityRules: AvailabilityRule[] = []) {
  return {
    anchor: FRIDAY_EVENING,
    appointments: [] as AppointmentResponse[],
    patientMap: new Map<string, string>(),
    availabilityRules,
    onSelectSlot: vi.fn(),
    onPeek: vi.fn(),
    onEdit: vi.fn(),
    onMove: vi.fn(),
    onContextMenu: vi.fn(),
    dayStart: 7,
    dayEnd: 20,
    rowHeightPx: 60,
  }
}

function column(container: HTMLElement, label: string): HTMLElement {
  const el = container.querySelector(`[aria-label^="${label} "]`)
  if (!el) throw new Error(`no column for ${label}`)
  return el as HTMLElement
}

function bands(col: HTMLElement): { top: string; height: string }[] {
  return [...col.querySelectorAll<HTMLElement>(".ed-unavailable")].map((band) => ({
    top: band.style.top,
    height: band.style.height,
  }))
}

describe("EditorialWeekView schedule", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] })
    vi.setSystemTime(FRIDAY_EVENING)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("draws working days that are already past as working time, marked past", () => {
    const { container } = render(<EditorialWeekView {...baseProps(MON_TO_THU_9_TO_5)} />)
    for (const day of ["Monday Sep 28", "Tuesday Sep 29", "Wednesday Sep 30", "Thursday Oct 1"]) {
      const col = column(container, day)
      // Hatched 7-9 and 5-8, open 9-5.
      expect(bands(col)).toEqual([
        { top: "0px", height: "120px" },
        { top: "600px", height: "180px" },
      ])
      // The whole day is behind the clinician, and washed as past.
      expect((col.querySelector(".ed-past") as HTMLElement).style.height).toBe("780px")
    }
  })

  it("marks today past up to now, and nothing after it", () => {
    const { container } = render(<EditorialWeekView {...baseProps(MON_TO_THU_9_TO_5)} />)
    const friday = column(container, "Friday Oct 2")
    expect((friday.querySelector(".ed-past") as HTMLElement).style.height).toBe("720px") // 12h
    const saturday = column(container, "Saturday Oct 3")
    expect(saturday.querySelector(".ed-past")).toBeNull()
  })

  it("shades a day with no hours from top to bottom", () => {
    const { container } = render(<EditorialWeekView {...baseProps(MON_TO_THU_9_TO_5)} />)
    expect(bands(column(container, "Saturday Oct 3"))).toEqual([{ top: "0px", height: "780px" }])
  })

  it("keeps open time to the stated end, with no band in front of a session", () => {
    const session = {
      id: "a1",
      patient_id: "p1",
      start_at: new Date(2026, 8, 28, 10, 0).toISOString(),
      end_at: new Date(2026, 8, 28, 10, 50).toISOString(),
      duration_minutes: 50,
      status: "confirmed",
    } as AppointmentResponse
    const { container } = render(
      <EditorialWeekView {...baseProps(MON_TO_THU_9_TO_5)} appointments={[session]} />,
    )
    expect(bands(column(container, "Monday Sep 28"))).toEqual([
      { top: "0px", height: "120px" },
      { top: "600px", height: "180px" },
    ])
  })

  it("labels only the blocked weekday's header with the rule's summarize() string", () => {
    const blockFriday = rule({ rule_type: "block_day_of_week", params: { day_of_week: 4 } })
    render(<EditorialWeekView {...baseProps([...MON_TO_THU_9_TO_5, blockFriday])} />)
    expect(screen.getAllByText("Friday blocked")).toHaveLength(1)
  })

  it("renders no shading and no label for a practice with no rules", () => {
    const { container } = render(<EditorialWeekView {...baseProps([])} />)
    expect(screen.queryByText("Friday blocked")).not.toBeInTheDocument()
    expect(container.querySelectorAll(".ed-unavailable")).toHaveLength(0)
  })
})
