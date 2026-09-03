// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"

// Mutable so a test can flip the deployment toggle; hoisted because vi.mock
// factories run before the module body.
const runtimeConfig = vi.hoisted(() => ({ googleCalendarEnabled: false }))

vi.mock("@/lib/config", () => ({ useConfig: () => runtimeConfig }))

vi.mock("../../GoogleCalendarSettings", () => ({
  GoogleCalendarSettings: () => <div data-testid="google-calendar-settings" />,
}))
vi.mock("../../IntegrationSettings", () => ({
  IntegrationSettings: () => <div data-testid="integration-settings" />,
}))

import { CalendarsPage } from "../CalendarsPage"

/**
 * Google Calendar needs OAuth credentials the deployment supplies, so a build
 * without them must not offer a Connect button that can only fail.
 */
describe("CalendarsPage", () => {
  beforeEach(() => {
    runtimeConfig.googleCalendarEnabled = false
  })

  it("hides the Google Calendar card when the deployment has not enabled it", () => {
    render(<CalendarsPage />)

    expect(screen.queryByTestId("google-calendar-settings")).not.toBeInTheDocument()
  })

  it("shows the Google Calendar card once the deployment enables it", () => {
    runtimeConfig.googleCalendarEnabled = true

    render(<CalendarsPage />)

    expect(screen.getByTestId("google-calendar-settings")).toBeInTheDocument()
  })

  it("keeps EHR calendar feeds available regardless of the Google toggle", () => {
    render(<CalendarsPage />)

    expect(screen.getByTestId("integration-settings")).toBeInTheDocument()
  })
})
