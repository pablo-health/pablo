// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/** Settings > Sessions & recording: the client video link switch sits in the Video sessions card. */

import { describe, expect, it, vi } from "vitest"
import { render, screen, within } from "@testing-library/react"

import { SessionsPage } from "../SessionsPage"

vi.mock("../../TelehealthSettings", () => ({ TelehealthSettings: () => null }))
vi.mock("../../AiNotesConsentCard", () => ({ AiNotesConsentCard: () => null }))
vi.mock("../../settingsSlots.extensions", () => ({ SessionsRecordingCard: () => null }))
vi.mock("../../useSettingsPreferences", () => ({
  useSettingsPreferences: () => ({ preferences: undefined, save: vi.fn(), isSaving: false }),
}))
vi.mock("@/hooks/useClientVideoLink", () => ({
  useClientVideoLinkSetting: () => ({
    data: { send_clients_video_link: true, available: true, can_change: true },
  }),
  useUpdateClientVideoLinkSetting: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
}))

describe("SessionsPage", () => {
  it("shows the client video link switch, on, in the Video sessions card", () => {
    render(<SessionsPage />)

    const card = screen.getByRole("region", { name: "Video sessions" })
    const toggle = within(card).getByRole("switch", { name: "Send clients their video link" })
    expect(toggle).toHaveAttribute("aria-checked", "true")
  })
})
