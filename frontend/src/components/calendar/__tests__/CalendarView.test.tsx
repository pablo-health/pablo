// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { CalendarView } from "../CalendarView"

// Use a spy to capture FullCalendar props without mutating module-scoped state
const calendarSpy = vi.fn()

vi.mock("@fullcalendar/react", () => {
  const MockCalendar = Object.assign(
    vi.fn((props: Record<string, unknown>) => {
      calendarSpy(props)
      return <div data-testid="fullcalendar" />
    }),
    { displayName: "MockFullCalendar" }
  )
  return { default: MockCalendar }
})

vi.mock("@fullcalendar/daygrid", () => ({ default: {} }))
vi.mock("@fullcalendar/timegrid", () => ({ default: {} }))
vi.mock("@fullcalendar/interaction", () => ({ default: {} }))

vi.mock("@/hooks/useAppointments", () => ({
  useAppointmentList: () => ({ data: null }),
  useUpdateAppointment: () => ({ mutate: vi.fn() }),
}))

vi.mock("@/hooks/usePatients", () => ({
  usePatientList: () => ({
    data: {
      data: [
        { id: "p1", first_name: "Jane", last_name: "Doe" },
        { id: "p2", first_name: "John", last_name: "Smith" },
      ],
    },
  }),
}))

vi.mock("@/lib/config", () => ({
  useConfig: () => ({ dataMode: "api" }),
}))

function lastCalendarProps(): Record<string, unknown> {
  return calendarSpy.mock.calls.at(-1)?.[0] ?? {}
}

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
    calendarSpy.mockClear()
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

      expect(lastCalendarProps().scrollTime).toBe("08:00:00")
      expect(lastCalendarProps().businessHours).toEqual({
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

      expect(lastCalendarProps().scrollTime).toBe("09:00:00")
      expect(lastCalendarProps().businessHours).toEqual({
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

      expect(lastCalendarProps().slotMinTime).toBe("00:00:00")
      expect(lastCalendarProps().slotMaxTime).toBe("24:00:00")
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

      expect(lastCalendarProps().scrollTime).toBe("06:00:00")
      expect(lastCalendarProps().businessHours).toEqual({
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

      expect(lastCalendarProps().scrollTime).toBe("14:00:00")
      expect(lastCalendarProps().businessHours).toEqual({
        daysOfWeek: [1, 2, 3, 4, 5],
        startTime: "14:00:00",
        endTime: "22:00:00",
      })
    })

    it("uses fixed height so scrollTime is honored", () => {
      render(
        <CalendarView onSelectSlot={vi.fn()} onSelectAppointment={vi.fn()} />,
        { wrapper: createWrapper() }
      )

      expect(lastCalendarProps().height).toBe(700)
    })
  })

  describe("Default View", () => {
    it("uses timeGridWeek by default", () => {
      render(
        <CalendarView onSelectSlot={vi.fn()} onSelectAppointment={vi.fn()} />,
        { wrapper: createWrapper() }
      )
      expect(lastCalendarProps().initialView).toBe("timeGridWeek")
    })

    it("respects defaultView prop", () => {
      render(
        <CalendarView
          onSelectSlot={vi.fn()}
          onSelectAppointment={vi.fn()}
          defaultView="timeGridDay"
        />,
        { wrapper: createWrapper() }
      )
      expect(lastCalendarProps().initialView).toBe("timeGridDay")
    })
  })

  describe("New Appointment Button", () => {
    it("adds custom button when onCreateNew is provided", () => {
      render(
        <CalendarView
          onSelectSlot={vi.fn()}
          onSelectAppointment={vi.fn()}
          onCreateNew={vi.fn()}
        />,
        { wrapper: createWrapper() }
      )
      const props = lastCalendarProps()
      expect(props.customButtons).toHaveProperty("newAppointment")
      const toolbar = props.headerToolbar as { right: string }
      expect(toolbar.right).toContain("newAppointment")
    })

    it("does not add custom button when onCreateNew is not provided", () => {
      render(
        <CalendarView onSelectSlot={vi.fn()} onSelectAppointment={vi.fn()} />,
        { wrapper: createWrapper() }
      )
      const props = lastCalendarProps()
      expect(props.customButtons).toBeUndefined()
    })
  })

  describe("View Change Callback", () => {
    it("passes viewDidMount handler when onViewChange is provided", () => {
      render(
        <CalendarView
          onSelectSlot={vi.fn()}
          onSelectAppointment={vi.fn()}
          onViewChange={vi.fn()}
        />,
        { wrapper: createWrapper() }
      )
      expect(lastCalendarProps().viewDidMount).toBeTypeOf("function")
    })
  })

  describe("Time Navigation", () => {
    it("renders earlier and later scroll buttons", () => {
      const { getByLabelText } = render(
        <CalendarView onSelectSlot={vi.fn()} onSelectAppointment={vi.fn()} />,
        { wrapper: createWrapper() }
      )

      expect(getByLabelText("Scroll to earlier times")).toBeInTheDocument()
      expect(getByLabelText("Scroll to later times")).toBeInTheDocument()
    })
  })
})
