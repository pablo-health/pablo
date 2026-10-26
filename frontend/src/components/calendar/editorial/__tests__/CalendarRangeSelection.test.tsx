// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { differenceInCalendarDays, format } from "date-fns"
import { EditorialCalendar } from "../EditorialCalendar"
import { ToastProvider } from "@/components/ui/Toast"
import type { CalendarRangeActionsProps } from "@/components/calendar/CalendarRangeActions"
import type { AppointmentResponse } from "@/types/scheduling"

const APPOINTMENTS: AppointmentResponse[] = []

vi.mock("@/hooks/useAvailability", () => ({
  useAvailabilityRules: () => ({ data: { data: [], total: 0 } }),
}))
vi.mock("@/hooks/useAppointments", () => ({
  useAppointmentList: () => ({ data: { data: APPOINTMENTS } }),
  useUpdateAppointment: () => ({ mutate: vi.fn() }),
}))
vi.mock("@/hooks/usePatients", () => ({
  usePatientList: () => ({ data: { data: [] } }),
}))
vi.mock("@/lib/config", () => ({
  useConfig: () => ({ dataMode: "api" }),
}))
// Stand in for a build that fills the slot, so the test sees what it is given.
vi.mock("@/components/calendar/CalendarRangeActions", () => ({
  CalendarRangeActions: ({ startDate, endDate, onDone }: CalendarRangeActionsProps) => (
    <button type="button" data-testid="range-action" onClick={onDone}>
      {startDate} to {endDate}
    </button>
  ),
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

function renderCalendar(view: "week" | "month" = "week") {
  return render(
    <EditorialCalendar
      theme="light"
      defaultView={view}
      onSelectSlot={vi.fn()}
      onSelectAppointment={vi.fn()}
      onCreateNew={vi.fn()}
    />,
    { wrapper: wrap() },
  )
}

function dayHeaders(): HTMLElement[] {
  return screen.getAllByRole("button", { name: /^Select / })
}

function appointmentAt(hour: number, overrides: Partial<AppointmentResponse>): AppointmentResponse {
  const start = new Date()
  start.setHours(hour, 0, 0, 0)
  return {
    id: `a-${hour}`,
    patient_id: "p1",
    patient_name: `Client ${hour}`,
    title: "Session",
    start_at: start.toISOString(),
    end_at: new Date(start.getTime() + 50 * 60_000).toISOString(),
    duration_minutes: 50,
    status: "confirmed",
    session_type: "individual",
    video_link: null,
    notes: null,
    ...overrides,
  } as AppointmentResponse
}

beforeEach(() => {
  APPOINTMENTS.length = 0
})

describe("selecting days on the calendar", () => {
  it("shows nothing until a day is selected", () => {
    renderCalendar()
    expect(screen.queryByTestId("calendar-range-selection")).not.toBeInTheDocument()
  })

  it("dragging across day headers selects the range and hands it to the slot", () => {
    renderCalendar()
    const [, monday, , wednesday] = dayHeaders()

    fireEvent.pointerDown(monday, { button: 0 })
    fireEvent.pointerEnter(wednesday)
    fireEvent.pointerUp(window)

    const bar = screen.getByTestId("calendar-range-selection")
    const [start, end] = within(bar).getByTestId("range-action").textContent!.split(" to ")
    expect(start).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(differenceInCalendarDays(new Date(`${end}T12:00`), new Date(`${start}T12:00`))).toBe(2)
    expect(dayHeaders().map((d) => d.getAttribute("aria-pressed"))).toEqual([
      "false",
      "true",
      "true",
      "true",
      "false",
      "false",
      "false",
    ])
  })

  it("a click selects one day, and the slot can let go of it", () => {
    renderCalendar()
    const [, monday] = dayHeaders()

    fireEvent.pointerDown(monday, { button: 0 })
    fireEvent.pointerUp(window)

    const action = screen.getByTestId("range-action")
    const [start, end] = action.textContent!.split(" to ")
    expect(start).toBe(end)

    fireEvent.click(action)
    expect(screen.queryByTestId("calendar-range-selection")).not.toBeInTheDocument()
  })

  it("Escape and the clear button let go of the selection", () => {
    renderCalendar()
    fireEvent.keyDown(dayHeaders()[2], { key: "Enter" })
    expect(screen.getByTestId("calendar-range-selection")).toBeInTheDocument()

    fireEvent.keyDown(window, { key: "Escape" })
    expect(screen.queryByTestId("calendar-range-selection")).not.toBeInTheDocument()

    fireEvent.keyDown(dayHeaders()[2], { key: "Enter" })
    fireEvent.click(screen.getByRole("button", { name: "Clear selected dates" }))
    expect(screen.queryByTestId("calendar-range-selection")).not.toBeInTheDocument()
  })

  it("in month view a plain click still opens the day rather than selecting it", () => {
    renderCalendar("month")
    // The sidebar's mini month carries the same labels; the grid's cells are
    // the ones that are not <button>s.
    const today = screen
      .getAllByRole("button", { name: format(new Date(), "PPPP") })
      .find((el) => el.tagName === "DIV")!

    fireEvent.pointerDown(today, { button: 0 })
    fireEvent.pointerUp(window)
    fireEvent.click(today)

    expect(screen.queryByTestId("calendar-range-selection")).not.toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /day/i })).toHaveAttribute("aria-selected", "true")
  })

  it("in month view a drag across days selects them", () => {
    renderCalendar("month")
    const cells = screen
      .getAllByRole("button")
      .filter((el) => el.tagName === "DIV" && /\b20\d\d\b/.test(el.getAttribute("aria-label") ?? ""))

    fireEvent.pointerDown(cells[8], { button: 0 })
    fireEvent.pointerEnter(cells[9])
    fireEvent.pointerEnter(cells[12])
    fireEvent.pointerUp(window)

    expect(screen.getByTestId("calendar-range-selection")).toBeInTheDocument()
    expect(cells.filter((c) => c.dataset.rangeSelected === "true")).toHaveLength(5)
  })
})

describe("held and skipped on the calendar", () => {
  it("shows a held time and a skipped week by default, and hides an ordinary cancellation", () => {
    const expires = new Date()
    expires.setDate(expires.getDate() + 2)
    APPOINTMENTS.push(
      appointmentAt(9, { status: "pending", pending_expires_at: expires.toISOString() }),
      appointmentAt(11, {
        status: "cancelled",
        skipped: true,
        recurring_appointment_id: "series-1",
      }),
      appointmentAt(13, { status: "cancelled" }),
    )

    renderCalendar()

    const notes = screen.getAllByTestId("event-note").map((n) => n.textContent)
    expect(notes).toEqual([`Held until ${format(expires, "EEE")}`, "Skipped"])
    expect(screen.getByRole("button", { name: /^Client 11 .*Skipped/ })).toHaveClass("ed-skipped")
    expect(screen.getByRole("button", { name: /^Client 9 .*Held until/ })).toHaveClass("ed-held")
    expect(screen.queryByText("Client 13")).not.toBeInTheDocument()
  })
})
