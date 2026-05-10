// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { EditorialCalendar } from "../EditorialCalendar"
import type { AppointmentResponse } from "@/types/scheduling"

const APPOINTMENTS: AppointmentResponse[] = []

vi.mock("@/hooks/useAppointments", () => ({
  useAppointmentList: () => ({ data: { data: APPOINTMENTS } }),
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
    onThemeChange: vi.fn(),
    style: "editorial" as const,
    onStyleChange: vi.fn(),
    onSelectSlot: vi.fn(),
    onSelectAppointment: vi.fn(),
    onCreateNew: vi.fn(),
  }
}

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

  it("toggles theme via the appearance switch", () => {
    const props = defaults()
    render(<EditorialCalendar {...props} />, { wrapper: wrap() })
    fireEvent.click(screen.getByRole("button", { name: /^dark$/i }))
    expect(props.onThemeChange).toHaveBeenCalledWith("dark")
  })

  it("toggles style via the appearance switch", () => {
    const props = defaults()
    render(<EditorialCalendar {...props} />, { wrapper: wrap() })
    fireEvent.click(screen.getByRole("button", { name: /^classic$/i }))
    expect(props.onStyleChange).toHaveBeenCalledWith("classic")
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
})
