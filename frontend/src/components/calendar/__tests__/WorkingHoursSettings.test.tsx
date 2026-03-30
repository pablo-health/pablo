// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { WorkingHoursSettings, formatHour } from "../WorkingHoursSettings"
import type { UserPreferences } from "@/lib/api/users"

function createPreferences(
  overrides: Partial<UserPreferences> = {}
): UserPreferences {
  return {
    default_video_platform: "zoom",
    default_session_type: "individual",
    default_duration_minutes: 50,
    auto_transcribe: true,
    quality_preset: "balanced",
    therapist_display_name: null,
    working_hours_start: 8,
    working_hours_end: 18,
    calendar_default_view: "week",
    ...overrides,
  }
}

describe("WorkingHoursSettings", () => {
  describe("Rendering", () => {
    it("renders heading and description", () => {
      render(
        <WorkingHoursSettings
          preferences={createPreferences()}
          onSave={vi.fn()}
          isSaving={false}
        />
      )
      expect(screen.getByText("Working Hours")).toBeInTheDocument()
      expect(
        screen.getByText(/Set your typical working hours/)
      ).toBeInTheDocument()
    })

    it("displays current start and end hours", () => {
      render(
        <WorkingHoursSettings
          preferences={createPreferences({
            working_hours_start: 9,
            working_hours_end: 17,
          })}
          onSave={vi.fn()}
          isSaving={false}
        />
      )
      expect(screen.getByText("9:00 AM")).toBeInTheDocument()
      expect(screen.getByText("5:00 PM")).toBeInTheDocument()
    })

    it("renders start and end labels", () => {
      render(
        <WorkingHoursSettings
          preferences={createPreferences()}
          onSave={vi.fn()}
          isSaving={false}
        />
      )
      expect(screen.getByText("Start")).toBeInTheDocument()
      expect(screen.getByText("End")).toBeInTheDocument()
    })
  })

  describe("Interactions", () => {
    it("calls onSave when start hour changes", async () => {
      const onSave = vi.fn()
      const user = userEvent.setup()

      render(
        <WorkingHoursSettings
          preferences={createPreferences({
            working_hours_start: 8,
            working_hours_end: 18,
          })}
          onSave={onSave}
          isSaving={false}
        />
      )

      // Click the start time trigger
      await user.click(screen.getByRole("combobox", { name: /start/i }))
      // Select 9:00 AM
      await user.click(screen.getByRole("option", { name: "9:00 AM" }))

      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({ working_hours_start: 9 })
      )
    })

    it("calls onSave when end hour changes", async () => {
      const onSave = vi.fn()
      const user = userEvent.setup()

      render(
        <WorkingHoursSettings
          preferences={createPreferences({
            working_hours_start: 8,
            working_hours_end: 18,
          })}
          onSave={onSave}
          isSaving={false}
        />
      )

      await user.click(screen.getByRole("combobox", { name: /end/i }))
      await user.click(screen.getByRole("option", { name: "5:00 PM" }))

      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({ working_hours_end: 17 })
      )
    })

    it("disables selects when saving", () => {
      render(
        <WorkingHoursSettings
          preferences={createPreferences()}
          onSave={vi.fn()}
          isSaving={true}
        />
      )

      const triggers = screen.getAllByRole("combobox")
      triggers.forEach((trigger) => {
        expect(trigger).toBeDisabled()
      })
    })
  })
})

describe("formatHour", () => {
  it("formats midnight as 12:00 AM", () => {
    expect(formatHour(0)).toBe("12:00 AM")
    expect(formatHour(24)).toBe("12:00 AM")
  })

  it("formats noon as 12:00 PM", () => {
    expect(formatHour(12)).toBe("12:00 PM")
  })

  it("formats morning hours", () => {
    expect(formatHour(1)).toBe("1:00 AM")
    expect(formatHour(8)).toBe("8:00 AM")
    expect(formatHour(11)).toBe("11:00 AM")
  })

  it("formats afternoon hours", () => {
    expect(formatHour(13)).toBe("1:00 PM")
    expect(formatHour(17)).toBe("5:00 PM")
    expect(formatHour(23)).toBe("11:00 PM")
  })
})
