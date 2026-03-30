// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { SessionDefaults } from "../SessionDefaults"
import type { UserPreferences } from "@/lib/api/users"

function createPreferences(overrides: Partial<UserPreferences> = {}): UserPreferences {
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

describe("SessionDefaults", () => {
  it("renders all three settings labels", () => {
    render(
      <SessionDefaults preferences={createPreferences()} onSave={vi.fn()} isSaving={false} />
    )

    expect(screen.getByText("Session Type")).toBeInTheDocument()
    expect(screen.getByText("Duration")).toBeInTheDocument()
    expect(screen.getByText("Video Platform")).toBeInTheDocument()
  })

  it("displays current session type", () => {
    render(
      <SessionDefaults preferences={createPreferences()} onSave={vi.fn()} isSaving={false} />
    )
    expect(screen.getByText("Individual")).toBeInTheDocument()
  })

  it("calls onSave when session type changes", async () => {
    const onSave = vi.fn()
    const user = userEvent.setup()

    render(
      <SessionDefaults preferences={createPreferences()} onSave={onSave} isSaving={false} />
    )

    await user.click(screen.getByRole("combobox", { name: /session type/i }))
    await user.click(screen.getByRole("option", { name: "Couples" }))

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ default_session_type: "couples" })
    )
  })

  it("disables all selects when saving", () => {
    render(
      <SessionDefaults preferences={createPreferences()} onSave={vi.fn()} isSaving={true} />
    )

    const triggers = screen.getAllByRole("combobox")
    triggers.forEach((trigger) => {
      expect(trigger).toBeDisabled()
    })
  })
})
