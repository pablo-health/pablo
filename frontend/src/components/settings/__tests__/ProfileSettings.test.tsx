// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { ProfileSettings } from "../ProfileSettings"
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

describe("ProfileSettings", () => {
  it("renders display name input", () => {
    render(
      <ProfileSettings preferences={createPreferences()} onSave={vi.fn()} isSaving={false} />
    )
    expect(screen.getByLabelText("Display Name")).toBeInTheDocument()
  })

  it("shows existing display name", () => {
    render(
      <ProfileSettings
        preferences={createPreferences({ therapist_display_name: "Dr. Smith" })}
        onSave={vi.fn()}
        isSaving={false}
      />
    )
    expect(screen.getByDisplayValue("Dr. Smith")).toBeInTheDocument()
  })

  it("shows save button only when value changes", async () => {
    const user = userEvent.setup()
    render(
      <ProfileSettings preferences={createPreferences()} onSave={vi.fn()} isSaving={false} />
    )

    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument()

    await user.type(screen.getByLabelText("Display Name"), "Dr. Jones")
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument()
  })

  it("calls onSave with updated preferences", async () => {
    const onSave = vi.fn()
    const user = userEvent.setup()

    render(
      <ProfileSettings preferences={createPreferences()} onSave={onSave} isSaving={false} />
    )

    await user.type(screen.getByLabelText("Display Name"), "Dr. Jones")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ therapist_display_name: "Dr. Jones" })
    )
  })
})
