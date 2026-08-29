// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { EditorialCalendar } from "../EditorialCalendar"
import type { AppointmentResponse } from "@/types/scheduling"

const APPOINTMENTS: AppointmentResponse[] = []
const updateMutate = vi.fn()

vi.mock("@/hooks/useAppointments", () => ({
  useAppointmentList: () => ({ data: { data: APPOINTMENTS } }),
  useUpdateAppointment: () => ({ mutate: updateMutate }),
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

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
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

beforeEach(() => {
  APPOINTMENTS.length = 0
  updateMutate.mockClear()
})

describe("EditorialCalendar", () => {
  it("renders the editorial canvas with day/week/month tabs", () => {
    render(<EditorialCalendar {...defaults()} />, { wrapper: wrap() })
    expect(screen.getByRole("tab", { name: /day/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /week/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /month/i })).toBeInTheDocument()
  })

  it("switches to day view when the Day tab is clicked", () => {
    render(<EditorialCalendar {...defaults()} />, { wrapper: wrap() })
    fireEvent.click(screen.getByRole("tab", { name: /day/i }))
    expect(screen.getByRole("tab", { name: /day/i })).toHaveAttribute(
      "aria-selected",
      "true",
    )
  })

  it("invokes onCreateNew when the sidebar CTA is clicked", () => {
    const props = defaults()
    render(<EditorialCalendar {...props} />, { wrapper: wrap() })
    fireEvent.click(screen.getByRole("button", { name: /new appointment/i }))
    expect(props.onCreateNew).toHaveBeenCalledTimes(1)
  })

  it("renders weekday header in week view by default", () => {
    render(<EditorialCalendar {...defaults()} />, { wrapper: wrap() })
    // each header cell renders "EEE d" (e.g. "Sun 10") inline
    expect(
      screen.getAllByText(/^(Sun|Mon|Tue|Wed|Thu|Fri|Sat) \d{1,2}$/),
    ).toHaveLength(7)
  })

  it("renders 42 day cells in month view", () => {
    render(
      <EditorialCalendar {...defaults()} defaultView="month" />,
      { wrapper: wrap() },
    )
    const cells = screen.getAllByRole("button").filter((el) => {
      const label = el.getAttribute("aria-label") ?? ""
      return /\b(20\d\d)\b/.test(label) && /[A-Za-z]+ \d{1,2}/.test(label)
    })
    expect(cells.length).toBeGreaterThanOrEqual(42)
  })

  it("labels an event from the payload's patient_name even when the patient is missing from the patient list", () => {
    // The patient list is paginated; a patient past the first page never
    // reaches patientMap. The payload's server-resolved name must win so the
    // event still carries the patient's name (not the title fallback).
    const start = new Date()
    start.setHours(10, 0, 0, 0)
    const end = new Date(start.getTime() + 50 * 60_000)
    APPOINTMENTS.push({
      id: "a9",
      patient_id: "p-not-on-first-page",
      patient_name: "Riley Nguyen",
      title: "Riley Nguyen — Individual",
      start_at: start.toISOString(),
      end_at: end.toISOString(),
      duration_minutes: 50,
      status: "confirmed",
      session_type: "individual",
      video_link: null,
      notes: null,
    } as AppointmentResponse)

    render(
      <EditorialCalendar {...defaults()} defaultView="day" />,
      { wrapper: wrap() },
    )

    expect(screen.getByText("Riley Nguyen")).toBeInTheDocument()
  })

  it("right-click status menu marks a no-show via the update mutation", () => {
    // Place a confirmed appointment at 10:00 today so it lands inside the
    // day-view working-hours window.
    const start = new Date()
    start.setHours(10, 0, 0, 0)
    const end = new Date(start.getTime() + 50 * 60_000)
    APPOINTMENTS.push({
      id: "a1",
      patient_id: "p1",
      title: "Jane Doe — Individual",
      start_at: start.toISOString(),
      end_at: end.toISOString(),
      duration_minutes: 50,
      status: "confirmed",
      session_type: "individual",
      video_link: null,
      notes: null,
    } as AppointmentResponse)

    render(
      <EditorialCalendar {...defaults()} defaultView="day" />,
      { wrapper: wrap() },
    )

    const event = document.querySelector('[data-event="1"]') as HTMLElement
    expect(event).not.toBeNull()
    fireEvent.contextMenu(event)

    // Menu opens with the current status checked and the four "Mark as" items.
    const confirmedItem = screen.getByRole("menuitemradio", { name: /confirmed/i })
    expect(confirmedItem).toHaveAttribute("aria-checked", "true")

    fireEvent.click(screen.getByRole("menuitemradio", { name: /no-show/i }))

    expect(updateMutate).toHaveBeenCalledTimes(1)
    expect(updateMutate).toHaveBeenCalledWith({
      appointmentId: "a1",
      data: { status: "no_show" },
    })
    // Menu closes after selecting a status.
    expect(screen.queryByRole("menu")).not.toBeInTheDocument()
  })

  it("re-selecting the current status does not call the mutation", () => {
    const start = new Date()
    start.setHours(10, 0, 0, 0)
    const end = new Date(start.getTime() + 50 * 60_000)
    APPOINTMENTS.push({
      id: "a1",
      patient_id: "p1",
      title: "Jane Doe — Individual",
      start_at: start.toISOString(),
      end_at: end.toISOString(),
      duration_minutes: 50,
      status: "confirmed",
      session_type: "individual",
      video_link: null,
      notes: null,
    } as AppointmentResponse)

    render(
      <EditorialCalendar {...defaults()} defaultView="day" />,
      { wrapper: wrap() },
    )

    const event = document.querySelector('[data-event="1"]') as HTMLElement
    fireEvent.contextMenu(event)
    fireEvent.click(screen.getByRole("menuitemradio", { name: /confirmed/i }))

    expect(updateMutate).not.toHaveBeenCalled()
    expect(screen.queryByRole("menu")).not.toBeInTheDocument()
  })

  it("defaults to balanced density (54px rows) when no density prop is passed", () => {
    const { container } = render(<EditorialCalendar {...defaults()} />, { wrapper: wrap() })
    const canvas = container.querySelector("[data-editorial-theme]") as HTMLElement
    expect(canvas).toHaveAttribute("data-density", "balanced")
    expect(canvas.style.getPropertyValue("--ed-row-h")).toBe("54px")
  })

  it("applies compact density (44px rows, 10px stack gap) via inline style", () => {
    const { container } = render(
      <EditorialCalendar {...defaults()} density="compact" />,
      { wrapper: wrap() },
    )
    const canvas = container.querySelector("[data-editorial-theme]") as HTMLElement
    expect(canvas).toHaveAttribute("data-density", "compact")
    expect(canvas.style.getPropertyValue("--ed-row-h")).toBe("44px")
    expect(canvas.style.getPropertyValue("--ed-stack-gap")).toBe("10px")
  })

  it("positions a 10:00 event and passes drag rowHeightPx per density", () => {
    const start = new Date()
    start.setHours(10, 0, 0, 0)
    const end = new Date(start.getTime() + 50 * 60_000)
    APPOINTMENTS.push({
      id: "a-density",
      patient_id: "p1",
      title: "Jane Doe — Individual",
      start_at: start.toISOString(),
      end_at: end.toISOString(),
      duration_minutes: 50,
      status: "confirmed",
      session_type: "individual",
      video_link: null,
      notes: null,
    } as AppointmentResponse)

    const { rerender } = render(
      <EditorialCalendar {...defaults()} defaultView="day" density="balanced" />,
      { wrapper: wrap() },
    )
    // dayStart defaults to 7am, so 10:00 is 3 hours in: top = 3 * rowPx.
    let event = document.querySelector('[data-event="1"]') as HTMLElement
    expect(event.style.top).toBe(`${3 * 54}px`)

    rerender(<EditorialCalendar {...defaults()} defaultView="day" density="compact" />)
    event = document.querySelector('[data-event="1"]') as HTMLElement
    expect(event.style.top).toBe(`${3 * 44}px`)
  })

  it("positions a 10:00 event the same way in week view under each density", () => {
    const start = new Date()
    start.setHours(10, 0, 0, 0)
    const end = new Date(start.getTime() + 50 * 60_000)
    APPOINTMENTS.push({
      id: "a-density-week",
      patient_id: "p1",
      title: "Jane Doe — Individual",
      start_at: start.toISOString(),
      end_at: end.toISOString(),
      duration_minutes: 50,
      status: "confirmed",
      session_type: "individual",
      video_link: null,
      notes: null,
    } as AppointmentResponse)

    const { rerender } = render(
      <EditorialCalendar {...defaults()} defaultView="week" density="balanced" />,
      { wrapper: wrap() },
    )
    let event = document.querySelector('[data-event="1"]') as HTMLElement
    expect(event.style.top).toBe(`${3 * 54}px`)

    rerender(<EditorialCalendar {...defaults()} defaultView="week" density="compact" />)
    event = document.querySelector('[data-event="1"]') as HTMLElement
    expect(event.style.top).toBe(`${3 * 44}px`)
  })
})
