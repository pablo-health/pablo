// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { CalendarDensitySettings } from "../CalendarDensitySettings"
import type { UserPreferences } from "@/lib/api/users"

function createPreferences(overrides: Partial<UserPreferences> = {}): UserPreferences {
  return {
    default_video_platform: "zoom",
    default_session_type: "individual",
    default_duration_minutes: 50,
    auto_transcribe: true,
    quality_preset: "balanced",
    therapist_display_name: null,
    calendar_default_view: "week",
    timezone: "America/New_York",
    theme: "warm-paper",
    calendar_density: "balanced",
    ...overrides,
  }
}

describe("CalendarDensitySettings", () => {
  it("marks the option matching the current preference as checked", () => {
    render(
      <CalendarDensitySettings
        preferences={createPreferences({ calendar_density: "compact" })}
        onSave={vi.fn()}
        isSaving={false}
      />,
    )

    expect(screen.getByRole("radio", { name: /compact/i })).toHaveAttribute("aria-checked", "true")
    expect(screen.getByRole("radio", { name: /gentle/i })).toHaveAttribute("aria-checked", "false")
    expect(screen.getByRole("radio", { name: /balanced/i })).toHaveAttribute("aria-checked", "false")
  })

  it("calls onSave once with the new density when Gentle is clicked", async () => {
    const onSave = vi.fn()
    const user = userEvent.setup()
    const preferences = createPreferences({ calendar_density: "balanced" })

    render(<CalendarDensitySettings preferences={preferences} onSave={onSave} isSaving={false} />)

    await user.click(screen.getByRole("radio", { name: /gentle/i }))

    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave).toHaveBeenCalledWith({ ...preferences, calendar_density: "gentle" })
  })

  it("disables every option while saving", () => {
    render(
      <CalendarDensitySettings preferences={createPreferences()} onSave={vi.fn()} isSaving={true} />,
    )

    screen.getAllByRole("radio").forEach((radio) => {
      expect(radio).toBeDisabled()
    })
  })
})
