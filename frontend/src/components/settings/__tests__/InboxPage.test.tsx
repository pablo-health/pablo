// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Settings → Inbox: the remembered answer about a client's earlier messages,
 * shown and changed.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { renderWithProviders } from "@/test/renderWithProviders"
import { InboxPage } from "../pages/InboxPage"

const prefsApi = { getPreferences: vi.fn(), savePreferences: vi.fn() }
vi.mock("@/lib/api/users", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/users")>()),
  getPreferences: (...a: unknown[]) => prefsApi.getPreferences(...a),
  savePreferences: (...a: unknown[]) => prefsApi.savePreferences(...a),
}))

const PREFERENCES = {
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
}

describe("Settings → Inbox", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    prefsApi.savePreferences.mockImplementation((prefs: unknown) => Promise.resolve(prefs))
  })

  it("asks by default, and says what the choice is about", async () => {
    prefsApi.getPreferences.mockResolvedValue(PREFERENCES)
    renderWithProviders(<InboxPage />)

    expect(
      await screen.findByText("When I reply to a client, their earlier unanswered messages:"),
    ).toBeInTheDocument()
    expect(screen.getByRole("radio", { name: "Ask me" })).toHaveAttribute("aria-checked", "true")
    expect(screen.getAllByRole("radio").map((radio) => radio.textContent)).toEqual([
      "Ask me",
      "Mark handled too",
      "Leave open",
    ])
  })

  it("shows a remembered answer and changes it", async () => {
    prefsApi.getPreferences.mockResolvedValue({ ...PREFERENCES, inbox_reply_earlier_messages: "never" })
    renderWithProviders(<InboxPage />)

    expect(await screen.findByRole("radio", { name: "Leave open" })).toHaveAttribute("aria-checked", "true")
    await userEvent.click(screen.getByRole("radio", { name: "Mark handled too" }))

    await waitFor(() =>
      expect(prefsApi.savePreferences).toHaveBeenCalledWith(
        { ...PREFERENCES, inbox_reply_earlier_messages: "always" },
        undefined,
      ),
    )
  })
})
