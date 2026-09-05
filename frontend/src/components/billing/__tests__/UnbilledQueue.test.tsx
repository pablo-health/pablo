// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * UnbilledQueue Component Tests
 *
 * Covers the states the queue has to get right: nothing to show, a populated
 * list linking each row to its session, and a row whose amount is unresolved
 * (no rate set anywhere) rendering as unknown rather than free.
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { UnbilledQueue } from "../UnbilledQueue"
import type { UnbilledSessionItem } from "@/types/billing"

const useUnbilledQueue = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useBilling", () => ({
  useUnbilledQueue: (...args: unknown[]) => useUnbilledQueue(...args),
}))

vi.mock("@/hooks/usePreferences", () => ({
  useUserTimeZone: () => "America/New_York",
  formatInUserTimeZone: (
    date: Date | string,
    timeZone: string,
    options: Intl.DateTimeFormatOptions,
  ) => new Date(date).toLocaleDateString("en-US", { ...options, timeZone }),
}))

function item(overrides: Partial<UnbilledSessionItem> = {}): UnbilledSessionItem {
  return {
    session_id: "sess-1",
    patient_id: "patient-1",
    patient_name: "Ada Early",
    session_date: "2026-06-10T14:00:00Z",
    amount_cents: 15000,
    currency: "usd",
    ...overrides,
  }
}

describe("UnbilledQueue", () => {
  it("shows an empty state when there is nothing unbilled", () => {
    useUnbilledQueue.mockReturnValue({ data: { items: [] }, isLoading: false })
    render(<UnbilledQueue />)
    expect(screen.getByText("Nothing unbilled")).toBeInTheDocument()
  })

  it("lists each unbilled session with client, date and amount, linking to the session", () => {
    useUnbilledQueue.mockReturnValue({ data: { items: [item()] }, isLoading: false })
    render(<UnbilledQueue />)

    expect(screen.getByText("Ada Early")).toBeInTheDocument()
    expect(screen.getByText("$150.00")).toBeInTheDocument()
    const link = screen.getByRole("link")
    expect(link).toHaveAttribute("href", "/dashboard/sessions/sess-1")
  })

  it("states that amounts are what was charged and Stripe is the source of truth", () => {
    useUnbilledQueue.mockReturnValue({ data: { items: [item()] }, isLoading: false })
    render(<UnbilledQueue />)
    expect(screen.getByText(/Stripe is the source of truth/)).toBeInTheDocument()
  })

  it("renders an unresolved amount as unknown rather than free", () => {
    useUnbilledQueue.mockReturnValue({
      data: { items: [item({ amount_cents: null })] },
      isLoading: false,
    })
    render(<UnbilledQueue />)
    expect(screen.getByText("No rate set")).toBeInTheDocument()
  })
})
