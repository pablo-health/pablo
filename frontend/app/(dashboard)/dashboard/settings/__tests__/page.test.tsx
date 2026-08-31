// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import type { UserPreferences } from "@/lib/api/users"
import { ThemeProvider } from "@/components/theme/ThemeProvider"
import SettingsPage from "../page"

const PREFERENCES: UserPreferences = {
  default_video_platform: "zoom",
  default_session_type: "individual",
  default_duration_minutes: 50,
  auto_transcribe: true,
  quality_preset: "balanced",
  therapist_display_name: null,
  working_hours_start: 8,
  working_hours_end: 18,
  calendar_default_view: "week",
  timezone: "America/New_York",
  theme: "warm-paper",
  calendar_density: "balanced",
}

const saveMutate = vi.fn()

vi.mock("@/hooks/usePreferences", () => ({
  usePreferences: () => ({ data: PREFERENCES, isLoading: false, error: null }),
  useSavePreferences: () => ({ mutate: saveMutate, isPending: false }),
}))

vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: { practice_id: "practice-1", provider_type: "therapist" } }),
}))

// Mutable so a test can flip a deployment toggle; hoisted because vi.mock
// factories run before the module body.
const runtimeConfig = vi.hoisted(() => ({
  passkeysEnabled: false,
  publicBookingEnabled: false,
  googleCalendarEnabled: false,
}))

vi.mock("@/lib/config", () => ({
  useConfig: () => runtimeConfig,
}))

vi.mock("@/lib/api/users", () => ({
  getUserStatus: vi.fn(),
}))

// Everything below the Appearance section pulls in its own data layer; stub
// each to a marker div so this test stays focused on page composition.
vi.mock("@/components/settings/ProfileSettings", () => ({
  ProfileSettings: () => <div data-testid="profile-settings" />,
}))
vi.mock("@/components/settings/ProviderTypeSettings", () => ({
  ProviderTypeSettings: () => <div data-testid="provider-type-settings" />,
}))
vi.mock("@/components/calendar/WorkingHoursSettings", () => ({
  WorkingHoursSettings: () => <div data-testid="working-hours-settings" />,
}))
vi.mock("@/components/settings/AvailabilitySettings", () => ({
  AvailabilitySettings: () => <div data-testid="availability-settings" />,
}))
vi.mock("@/components/settings/BookingLinkSettings", () => ({
  BookingLinkSettings: () => <div data-testid="booking-link-settings" />,
}))
vi.mock("@/components/settings/SessionDefaults", () => ({
  SessionDefaults: () => <div data-testid="session-defaults" />,
}))
vi.mock("@/components/settings/PasskeySettings", () => ({
  PasskeySettings: () => <div data-testid="passkey-settings" />,
}))
vi.mock("@/components/settings/IntegrationSettings", () => ({
  IntegrationSettings: () => <div data-testid="integration-settings" />,
}))
vi.mock("@/components/settings/TranscriptionSettings", () => ({
  TranscriptionSettings: () => <div data-testid="transcription-settings" />,
}))
vi.mock("@/components/settings/AudioRetentionSettings", () => ({
  AudioRetentionSettings: () => <div data-testid="audio-retention-settings" />,
}))

describe("SettingsPage", () => {
  beforeEach(() => {
    runtimeConfig.googleCalendarEnabled = false
  })

  it("shows the calendar density control inside the Appearance section, alongside the theme switcher", () => {
    render(
      <ThemeProvider>
        <SettingsPage />
      </ThemeProvider>,
    )

    const appearanceHeading = screen.getByRole("heading", { name: "Appearance" })
    const appearanceSection = appearanceHeading.closest("section") as HTMLElement
    expect(appearanceSection).not.toBeNull()

    expect(
      appearanceSection.querySelector('[role="radiogroup"][aria-label="Calendar density"]'),
    ).not.toBeNull()
    expect(
      appearanceSection.querySelector('[role="radiogroup"][aria-label="Color theme"]'),
    ).not.toBeNull()
  })

  it("hides the Google Calendar section when the deployment has not enabled it", () => {
    render(
      <ThemeProvider>
        <SettingsPage />
      </ThemeProvider>,
    )

    expect(screen.queryByRole("heading", { name: "Google Calendar" })).toBeNull()
  })

  it("shows the Google Calendar section once the deployment enables it", () => {
    runtimeConfig.googleCalendarEnabled = true

    render(
      <ThemeProvider>
        <SettingsPage />
      </ThemeProvider>,
    )

    expect(screen.getByRole("heading", { name: "Google Calendar" })).toBeInTheDocument()
  })
})
