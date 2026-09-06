// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi } from "vitest"
import { render, fireEvent } from "@testing-library/react"
import { EditorialDayView } from "../EditorialDayView"
import type { AvailabilityRule, FreeSlotsResponse } from "@/types/availability"

// Friday, June 5 2026.
const FRIDAY = new Date(2026, 5, 5)

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

function freeSlots(overrides: Partial<FreeSlotsResponse> = {}): FreeSlotsResponse {
  return {
    date: "2026-06-05",
    duration_minutes: 50,
    slots: [],
    total: 0,
    configured: true,
    ...overrides,
  }
}

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

describe("EditorialDayView unavailable shading", () => {
  it("shades only the hours outside a partial day's free slots", () => {
    const props = baseProps()
    const { container } = render(
      <EditorialDayView
        {...props}
        freeSlots={freeSlots({ slots: [{ start: "2026-06-05T09:00:00", end: "2026-06-05T12:00:00" }] })}
      />,
    )
    const bands = container.querySelectorAll(".ed-unavailable")
    expect(bands).toHaveLength(2)
    // 7am-9am gap: top = 0, height = 2h * 60px
    expect((bands[0] as HTMLElement).style.top).toBe("0px")
    expect((bands[0] as HTMLElement).style.height).toBe("120px")
    // 12pm-8pm gap: top = 5h * 60px, height = 8h * 60px
    expect((bands[1] as HTMLElement).style.top).toBe("300px")
    expect((bands[1] as HTMLElement).style.height).toBe("480px")
  })

  it("shades the full column when a block_day_of_week rule blanks the day", () => {
    const props = baseProps()
    const blockFriday = rule({ rule_type: "block_day_of_week", params: { day_of_week: 4 } })
    const { container } = render(
      <EditorialDayView
        {...props}
        availabilityRules={[blockFriday]}
        freeSlots={freeSlots({ slots: [] })}
      />,
    )
    const bands = container.querySelectorAll(".ed-unavailable")
    expect(bands).toHaveLength(1)
    expect((bands[0] as HTMLElement).style.top).toBe("0px")
    expect((bands[0] as HTMLElement).style.height).toBe("780px") // 13h * 60px

    const canvas = container.querySelector("[data-daycanvas]") as HTMLElement
    expect(canvas.title).toContain("Friday blocked")
  })

  it("renders no shading and no tooltip when configured === false", () => {
    const props = baseProps()
    const blockFriday = rule({ rule_type: "block_day_of_week", params: { day_of_week: 4 } })
    const { container } = render(
      <EditorialDayView
        {...props}
        availabilityRules={[blockFriday]}
        freeSlots={freeSlots({ configured: false, slots: [] })}
      />,
    )
    expect(container.querySelectorAll(".ed-unavailable")).toHaveLength(0)
    const canvas = container.querySelector("[data-daycanvas]") as HTMLElement
    expect(canvas.title).toBe("")
  })

  it("renders no shading while free slots are still loading (freeSlots undefined)", () => {
    const props = baseProps()
    const { container } = render(<EditorialDayView {...props} freeSlots={undefined} />)
    expect(container.querySelectorAll(".ed-unavailable")).toHaveLength(0)
  })

  it("does not intercept slot-selection clicks — shading is presentation only", () => {
    const props = baseProps()
    const { container } = render(
      <EditorialDayView
        {...props}
        freeSlots={freeSlots({ slots: [{ start: "2026-06-05T09:00:00", end: "2026-06-05T12:00:00" }] })}
      />,
    )
    const bands = container.querySelectorAll(".ed-unavailable")
    expect(bands.length).toBeGreaterThan(0)
    bands.forEach((band) => expect(band.className).toContain("pointer-events-none"))

    const canvas = container.querySelector("[data-daycanvas]") as HTMLElement
    fireEvent.click(canvas, { clientY: 10 })
    expect(props.onSelectSlot).toHaveBeenCalledTimes(1)
  })
})
