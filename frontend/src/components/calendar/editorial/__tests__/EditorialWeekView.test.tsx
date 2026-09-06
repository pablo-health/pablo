// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { EditorialWeekView } from "../EditorialWeekView"
import type { AvailabilityRule, FreeSlotsResponse } from "@/types/availability"

// Sunday June 7 2026 anchors a week that contains Friday June 5... but a
// week view always starts on Sunday, so anchor mid-week instead: Wednesday
// June 3 2026 sits in the week of Sun May 31 - Sat Jun 6, which contains
// Friday June 5.
const WEDNESDAY = new Date(2026, 5, 3)

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

function freeSlotsFor(dateStr: string, overrides: Partial<FreeSlotsResponse> = {}): FreeSlotsResponse {
  return {
    date: dateStr,
    duration_minutes: 50,
    slots: [],
    total: 0,
    configured: true,
    ...overrides,
  }
}

const getFreeSlotsMock = vi.fn()

vi.mock("@/lib/api/availability", () => ({
  getFreeSlots: (date: string) => getFreeSlotsMock(date),
}))

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "TestQueryClientWrapper"
  return Wrapper
}

function baseProps(availabilityRules: AvailabilityRule[] = []) {
  return {
    anchor: WEDNESDAY,
    appointments: [],
    patientMap: new Map<string, string>(),
    availabilityRules,
    onSelectSlot: vi.fn(),
    onPeek: vi.fn(),
    onEdit: vi.fn(),
    onMove: vi.fn(),
    onContextMenu: vi.fn(),
  }
}

describe("EditorialWeekView unavailable shading", () => {
  it("labels only the blocked weekday's header with the rule's summarize() string", async () => {
    getFreeSlotsMock.mockImplementation((date: string) =>
      Promise.resolve(freeSlotsFor(date, { slots: date === "2026-06-05" ? [] : [{ start: `${date}T09:00:00`, end: `${date}T17:00:00` }] })),
    )
    const blockFriday = rule({ rule_type: "block_day_of_week", params: { day_of_week: 4 } })

    render(<EditorialWeekView {...baseProps([blockFriday])} />, { wrapper: wrap() })

    await waitFor(() => {
      expect(screen.getByText("Friday blocked")).toBeInTheDocument()
    })
    // Only the one blocked day gets a label.
    expect(screen.getAllByText("Friday blocked")).toHaveLength(1)
  })

  it("renders no shading and no label anywhere when configured === false", async () => {
    getFreeSlotsMock.mockResolvedValue(freeSlotsFor("2026-06-05", { configured: false, slots: [] }))
    const blockFriday = rule({ rule_type: "block_day_of_week", params: { day_of_week: 4 } })

    const { container } = render(<EditorialWeekView {...baseProps([blockFriday])} />, {
      wrapper: wrap(),
    })

    await waitFor(() => {
      expect(getFreeSlotsMock).toHaveBeenCalled()
    })
    expect(screen.queryByText("Friday blocked")).not.toBeInTheDocument()
    expect(container.querySelectorAll(".ed-unavailable")).toHaveLength(0)
  })
})
