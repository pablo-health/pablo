// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { AvailabilitySlotPicker } from "../AvailabilitySlotPicker"
import type { FreeSlotsResponse } from "@/types/availability"

let slotsData: FreeSlotsResponse | undefined
let slotsLoading = false

vi.mock("@/hooks/useAvailability", () => ({
  useFreeSlots: () => ({ data: slotsData, isLoading: slotsLoading }),
}))

function renderWithClient(props: Partial<Parameters<typeof AvailabilitySlotPicker>[0]> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const onSelect = vi.fn()
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <AvailabilitySlotPicker
        date="2026-08-27"
        duration={45}
        selectedTime=""
        onSelect={onSelect}
        {...props}
      />
    </QueryClientProvider>
  )
  return { onSelect, ...utils }
}

describe("AvailabilitySlotPicker", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    slotsData = undefined
    slotsLoading = false
  })

  it("lists open slots for the day", () => {
    slotsData = {
      date: "2026-08-27",
      duration_minutes: 45,
      total: 2,
      configured: true,
      slots: [
        { start: "2026-08-27T14:00:00Z", end: "2026-08-27T14:45:00Z" },
        { start: "2026-08-27T15:00:00Z", end: "2026-08-27T15:45:00Z" },
      ],
    }
    renderWithClient()

    expect(screen.getByRole("group", { name: "Open slots" })).toBeInTheDocument()
    expect(screen.getAllByRole("button")).toHaveLength(2)
  })

  it("fills the time when a slot is selected", async () => {
    const user = userEvent.setup()
    slotsData = {
      date: "2026-08-27",
      duration_minutes: 45,
      total: 1,
      configured: true,
      slots: [{ start: "2026-08-27T14:00:00Z", end: "2026-08-27T14:45:00Z" }],
    }
    const { onSelect } = renderWithClient()

    await user.click(screen.getAllByRole("button")[0])

    expect(onSelect).toHaveBeenCalledWith("14:00")
  })

  it("shows a no-openings state distinct from not-configured, with no settings link", () => {
    slotsData = {
      date: "2026-08-27",
      duration_minutes: 45,
      total: 0,
      configured: true,
      slots: [],
    }
    renderWithClient()

    expect(screen.getByText(/no openings on this day/i)).toBeInTheDocument()
    expect(screen.queryByRole("link")).not.toBeInTheDocument()
  })

  it("shows a not-configured onboarding prompt linking to availability settings", () => {
    slotsData = {
      date: "2026-08-27",
      duration_minutes: 45,
      total: 0,
      configured: false,
      slots: [],
    }
    renderWithClient()

    expect(screen.getByText(/haven't set up your availability yet/i)).toBeInTheDocument()
    const link = screen.getByRole("link", { name: /set it up/i })
    expect(link).toHaveAttribute("href", "/dashboard/settings")
  })
})
