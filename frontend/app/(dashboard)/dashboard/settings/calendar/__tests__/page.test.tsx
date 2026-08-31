// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import CalendarSetupPage from "../page"

// Mutable so a test can flip the deployment toggle; hoisted because vi.mock
// factories run before the module body.
const runtimeConfig = vi.hoisted(() => ({ googleCalendarEnabled: false }))

vi.mock("@/lib/config", () => ({
  useConfig: () => runtimeConfig,
}))

const notFound = vi.hoisted(() => vi.fn(() => {
  throw new Error("NEXT_NOT_FOUND")
}))

vi.mock("next/navigation", () => ({ notFound }))

vi.mock("@/components/calendar/connect/CalendarSetupWizard", () => ({
  CalendarSetupWizard: () => <div data-testid="calendar-setup-wizard" />,
}))

describe("CalendarSetupPage", () => {
  beforeEach(() => {
    runtimeConfig.googleCalendarEnabled = false
    notFound.mockClear()
  })

  it("404s when the deployment has not enabled calendar", () => {
    // The route is reachable by typing it even while the Settings link is
    // hidden, so the page has to refuse on its own.
    expect(() => render(<CalendarSetupPage />)).toThrow("NEXT_NOT_FOUND")
    expect(notFound).toHaveBeenCalled()
  })

  it("renders the wizard once the deployment enables calendar", () => {
    runtimeConfig.googleCalendarEnabled = true

    render(<CalendarSetupPage />)

    expect(screen.getByTestId("calendar-setup-wizard")).toBeInTheDocument()
    expect(notFound).not.toHaveBeenCalled()
  })
})
