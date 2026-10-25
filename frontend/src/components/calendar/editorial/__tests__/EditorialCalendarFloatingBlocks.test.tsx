// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The calendar asks for floating blocks on the days it shows, once the
 * practice has such a rule, and the band's menu writes through the hooks.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { addDays, format, startOfDay, startOfWeek } from "date-fns"
import { EditorialCalendar } from "../EditorialCalendar"
import { ToastProvider } from "@/components/ui/Toast"
import type { AvailabilityRule, FloatingBlock } from "@/types/availability"

const RULES: AvailabilityRule[] = []
const BLOCKS: FloatingBlock[] = []
const askedFor = vi.fn()
const setMutate = vi.fn()
const clearMutate = vi.fn()

vi.mock("@/hooks/useAvailability", () => ({
  useAvailabilityRules: () => ({ data: { data: RULES, total: RULES.length } }),
}))

vi.mock("@/hooks/useFloatingBlocks", () => ({
  useFloatingBlocksForDates: (dates: string[], enabled: boolean) => {
    askedFor(dates, enabled)
    return enabled ? BLOCKS : []
  },
  useSetFloatingBlockException: () => ({ mutate: setMutate, isPending: false }),
  useClearFloatingBlockException: () => ({ mutate: clearMutate, isPending: false }),
}))

vi.mock("@/hooks/useAppointments", () => ({
  useAppointmentList: () => ({ data: { data: [] } }),
  useUpdateAppointment: () => ({ mutate: vi.fn() }),
}))

vi.mock("@/hooks/usePatients", () => ({
  usePatientList: () => ({ data: { data: [] } }),
}))

vi.mock("@/lib/config", () => ({
  useConfig: () => ({ dataMode: "api" }),
}))

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>
      <ToastProvider>{children}</ToastProvider>
    </QueryClientProvider>
  )
  Wrapper.displayName = "TestQueryClientWrapper"
  return Wrapper
}

function defaults() {
  return {
    theme: "light" as const,
    onSelectSlot: vi.fn(),
    onSelectAppointment: vi.fn(),
    onCreateNew: vi.fn(),
  }
}

const LUNCH_RULE: AvailabilityRule = {
  id: "rule-lunch",
  user_id: "u1",
  rule_type: "floating_block",
  enforcement: "hard",
  params: {
    duration_minutes: 60,
    window_start: "11:30",
    window_end: "14:00",
    days_of_week: [0, 1, 2, 3, 4],
    label: "Lunch",
  },
  created_at: null,
  updated_at: null,
}

function weekDates(): string[] {
  const start = startOfWeek(new Date(), { weekStartsOn: 0 })
  return Array.from({ length: 7 }, (_, i) => format(addDays(start, i), "yyyy-MM-dd"))
}

/** A day in the visible week other than today, so its label is fixed. */
function notToday(): Date {
  const today = startOfDay(new Date())
  const start = startOfWeek(today, { weekStartsOn: 0 })
  return start.getTime() === today.getTime() ? addDays(start, 1) : start
}

function lunchOn(day: Date): FloatingBlock {
  const start = new Date(day)
  start.setHours(12, 15, 0, 0)
  const end = new Date(day)
  end.setHours(13, 15, 0, 0)
  return {
    rule_id: "rule-lunch",
    label: "Lunch",
    date: format(day, "yyyy-MM-dd"),
    state: "floating",
    duration_minutes: 60,
    window_start: "11:30",
    window_end: "14:00",
    start: start.toISOString(),
    end: end.toISOString(),
  }
}

beforeEach(() => {
  RULES.length = 0
  BLOCKS.length = 0
  askedFor.mockReset()
  setMutate.mockReset()
  clearMutate.mockReset()
})

describe("EditorialCalendar floating blocks", () => {
  it("asks for nothing while the practice has no floating block", () => {
    render(<EditorialCalendar {...defaults()} />, { wrapper: wrap() })
    expect(askedFor).toHaveBeenLastCalledWith(weekDates(), false)
  })

  it("asks for each day of the week and draws the band", () => {
    RULES.push(LUNCH_RULE)
    BLOCKS.push(lunchOn(notToday()))
    render(<EditorialCalendar {...defaults()} />, { wrapper: wrap() })
    expect(askedFor).toHaveBeenLastCalledWith(weekDates(), true)
    expect(screen.getByTestId("floating-block")).toHaveTextContent("Lunch")
  })

  it("skips that date through the exception write", () => {
    RULES.push(LUNCH_RULE)
    const day = notToday()
    BLOCKS.push(lunchOn(day))
    render(<EditorialCalendar {...defaults()} />, { wrapper: wrap() })
    fireEvent.click(screen.getByTestId("floating-block"))
    fireEvent.click(screen.getByRole("button", { name: "Skip this day" }))
    expect(setMutate).toHaveBeenCalledWith(
      { ruleId: "rule-lunch", date: format(day, "yyyy-MM-dd"), exception: { skip: true } },
      expect.anything(),
    )
  })

  it("asks for the one day in day view", () => {
    RULES.push(LUNCH_RULE)
    render(<EditorialCalendar {...defaults()} />, { wrapper: wrap() })
    fireEvent.click(screen.getByRole("tab", { name: /day/i }))
    expect(askedFor).toHaveBeenLastCalledWith([format(new Date(), "yyyy-MM-dd")], true)
  })

  it("asks for nothing in month view", () => {
    RULES.push(LUNCH_RULE)
    render(<EditorialCalendar {...defaults()} />, { wrapper: wrap() })
    fireEvent.click(screen.getByRole("tab", { name: /month/i }))
    expect(askedFor).toHaveBeenLastCalledWith([], true)
  })
})
