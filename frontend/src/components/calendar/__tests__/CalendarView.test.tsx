// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { CalendarView } from "../CalendarView"

// Mock FullCalendar to capture props
let lastCalendarProps: Record<string, unknown> = {}

vi.mock("@fullcalendar/react", () => ({
  default: (props: Record<string, unknown>) => {
    lastCalendarProps = props
    return <div data-testid="fullcalendar" />
  },
}))

vi.mock("@fullcalendar/daygrid", () => ({ default: {} }))
vi.mock("@fullcalendar/timegrid", () => ({ default: {} }))
vi.mock("@fullcalendar/interaction", () => ({ default: {} }))

vi.mock("@/hooks/useAppointments", () => ({
  useAppointmentList: () => ({ data: null }),
  useUpdateAppointment: () => ({ mutate: vi.fn() }),
}))

vi.mock("@/lib/config", () => ({
  useConfig: () => ({ dataMode: "api" }),
}))

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "TestQueryClientWrapper"
  return Wrapper
}

describe("CalendarView", () => {
  beforeEach(() => {
    lastCalendarProps = {}
  })

  it("renders FullCalendar", () => {
    const { getByTestId } = render(
      <CalendarView onSelectSlot={vi.fn()} onSelectAppointment={vi.fn()} />,
      { wrapper: createWrapper() }
    )
    expect(getByTestId("fullcalendar")).toBeInTheDocument()
  })

  describe("Working Hours", () => {
    it("uses default working hours (8am-6pm) when not provided", () => {
      render(
        <CalendarView onSelectSlot={vi.fn()} onSelectAppointment={vi.fn()} />,
        { wrapper: createWrapper() }
      )

      expect(lastCalendarProps.scrollTime).toBe("08:00:00")
      expect(lastCalendarProps.businessHours).toEqual({
        daysOfWeek: [1, 2, 3, 4, 5],
        startTime: "08:00:00",
        endTime: "18:00:00",
      })
    })

    it("uses custom working hours when provided", () => {
      render(
        <CalendarView
          onSelectSlot={vi.fn()}
          onSelectAppointment={vi.fn()}
          workingHoursStart={9}
          workingHoursEnd={17}
        />,
        { wrapper: createWrapper() }
      )

      expect(lastCalendarProps.scrollTime).toBe("09:00:00")
      expect(lastCalendarProps.businessHours).toEqual({
        daysOfWeek: [1, 2, 3, 4, 5],
        startTime: "09:00:00",
        endTime: "17:00:00",
      })
    })

    it("allows scrolling to full 24-hour range", () => {
      render(
        <CalendarView onSelectSlot={vi.fn()} onSelectAppointment={vi.fn()} />,
        { wrapper: createWrapper() }
      )

      expect(lastCalendarProps.slotMinTime).toBe("00:00:00")
      expect(lastCalendarProps.slotMaxTime).toBe("24:00:00")
    })

    it("handles early morning working hours", () => {
      render(
        <CalendarView
          onSelectSlot={vi.fn()}
          onSelectAppointment={vi.fn()}
          workingHoursStart={6}
          workingHoursEnd={14}
        />,
        { wrapper: createWrapper() }
      )

      expect(lastCalendarProps.scrollTime).toBe("06:00:00")
      expect(lastCalendarProps.businessHours).toEqual({
        daysOfWeek: [1, 2, 3, 4, 5],
        startTime: "06:00:00",
        endTime: "14:00:00",
      })
    })

    it("handles late evening working hours", () => {
      render(
        <CalendarView
          onSelectSlot={vi.fn()}
          onSelectAppointment={vi.fn()}
          workingHoursStart={14}
          workingHoursEnd={22}
        />,
        { wrapper: createWrapper() }
      )

      expect(lastCalendarProps.scrollTime).toBe("14:00:00")
      expect(lastCalendarProps.businessHours).toEqual({
        daysOfWeek: [1, 2, 3, 4, 5],
        startTime: "14:00:00",
        endTime: "22:00:00",
      })
    })
  })
})
