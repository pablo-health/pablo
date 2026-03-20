// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { TranscriptionSettings } from "../TranscriptionSettings"
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
    ...overrides,
  }
}

describe("TranscriptionSettings", () => {
  it("renders auto-transcribe checkbox", () => {
    render(
      <TranscriptionSettings preferences={createPreferences()} onSave={vi.fn()} isSaving={false} />
    )

    expect(
      screen.getByLabelText("Automatically transcribe uploaded recordings")
    ).toBeInTheDocument()
  })

  it("renders quality preset selector", () => {
    render(
      <TranscriptionSettings preferences={createPreferences()} onSave={vi.fn()} isSaving={false} />
    )

    expect(screen.getByText("Quality Preset")).toBeInTheDocument()
    expect(screen.getByText("Balanced")).toBeInTheDocument()
  })

  it("calls onSave when checkbox is toggled", async () => {
    const onSave = vi.fn()
    const user = userEvent.setup()

    render(
      <TranscriptionSettings
        preferences={createPreferences({ auto_transcribe: true })}
        onSave={onSave}
        isSaving={false}
      />
    )

    await user.click(screen.getByLabelText("Automatically transcribe uploaded recordings"))
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ auto_transcribe: false })
    )
  })

  it("calls onSave when quality preset changes", async () => {
    const onSave = vi.fn()
    const user = userEvent.setup()

    render(
      <TranscriptionSettings preferences={createPreferences()} onSave={onSave} isSaving={false} />
    )

    await user.click(screen.getByRole("combobox", { name: /quality preset/i }))
    await user.click(screen.getByRole("option", { name: "Accurate" }))

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ quality_preset: "accurate" })
    )
  })

  it("disables controls when saving", () => {
    render(
      <TranscriptionSettings preferences={createPreferences()} onSave={vi.fn()} isSaving={true} />
    )

    expect(screen.getByRole("checkbox")).toBeDisabled()
    expect(screen.getByRole("combobox")).toBeDisabled()
  })
})
