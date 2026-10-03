// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, it, expect, vi } from "vitest"
import { render, fireEvent } from "@testing-library/react"
import { EditorialDayView } from "../EditorialDayView"
import type { AvailabilityRule } from "@/types/availability"

// Friday, June 5 2026; the clock is set to the Monday before it, so the day
// is still ahead and nothing on it is past.
const FRIDAY = new Date(2026, 5, 5)
const MONDAY_BEFORE = new Date(2026, 5, 1, 12, 0)

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

const FRIDAY_9_TO_12 = rule({ params: { day_of_week: 4, start: "09:00", end: "12:00" } })

function baseProps() {
  return {
    anchor: FRIDAY,
    appointments: [],
    patientMap: new Map<string, string>(),
    availabilityRules: [] as AvailabilityRule[],
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

describe("EditorialDayView schedule shading", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] })
    vi.setSystemTime(MONDAY_BEFORE)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("shades only the hours outside the day's working hours", () => {
    const { container } = render(
      <EditorialDayView {...baseProps()} availabilityRules={[FRIDAY_9_TO_12]} />,
    )
    const bands = container.querySelectorAll(".ed-unavailable")
    expect(bands).toHaveLength(2)
    // 7am-9am: top = 0, height = 2h * 60px
    expect((bands[0] as HTMLElement).style.top).toBe("0px")
    expect((bands[0] as HTMLElement).style.height).toBe("120px")
    // 12pm-8pm: top = 5h * 60px, height = 8h * 60px — open right up to the
    // stated end, not to the last session that fits before it.
    expect((bands[1] as HTMLElement).style.top).toBe("300px")
    expect((bands[1] as HTMLElement).style.height).toBe("480px")
  })

  it("shades the full column when a block_day_of_week rule blanks the day", () => {
    const blockFriday = rule({ rule_type: "block_day_of_week", params: { day_of_week: 4 } })
    const { container } = render(
      <EditorialDayView {...baseProps()} availabilityRules={[FRIDAY_9_TO_12, blockFriday]} />,
    )
    const bands = container.querySelectorAll(".ed-unavailable")
    expect(bands).toHaveLength(1)
    expect((bands[0] as HTMLElement).style.top).toBe("0px")
    expect((bands[0] as HTMLElement).style.height).toBe("780px") // 13h * 60px

    const canvas = container.querySelector("[data-daycanvas]") as HTMLElement
    expect(canvas.title).toContain("Friday blocked")
  })

  it("renders no shading and no tooltip for a practice with no rules", () => {
    const { container } = render(<EditorialDayView {...baseProps()} />)
    expect(container.querySelectorAll(".ed-unavailable")).toHaveLength(0)
    const canvas = container.querySelector("[data-daycanvas]") as HTMLElement
    expect(canvas.title).toBe("")
  })

  it("washes a past day over as past, keeping its working hours open", () => {
    vi.setSystemTime(new Date(2026, 5, 6, 18, 0))
    const { container } = render(
      <EditorialDayView {...baseProps()} availabilityRules={[FRIDAY_9_TO_12]} />,
    )
    const past = container.querySelector(".ed-past") as HTMLElement
    expect(past.style.height).toBe("780px")
    expect(container.querySelectorAll(".ed-unavailable")).toHaveLength(2)
  })

  it("does not intercept slot-selection clicks — shading is presentation only", () => {
    const props = baseProps()
    const { container } = render(
      <EditorialDayView {...props} availabilityRules={[FRIDAY_9_TO_12]} />,
    )
    const bands = container.querySelectorAll(".ed-unavailable")
    expect(bands.length).toBeGreaterThan(0)
    bands.forEach((band) => expect(band.className).toContain("pointer-events-none"))

    const canvas = container.querySelector("[data-daycanvas]") as HTMLElement
    fireEvent.click(canvas, { clientY: 10 })
    expect(props.onSelectSlot).toHaveBeenCalledTimes(1)
  })
})
