// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Settings → Calendars: whether a session whose title is one client's full
 * name books on its own. Shows the server's default until the clinician
 * chooses, and saves the choice.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { renderWithProviders } from "@/test/renderWithProviders"
import { NameBookingSetting } from "../NameBookingSetting"

const api = { getPreferences: vi.fn(), savePreferences: vi.fn(), getUserStatus: vi.fn() }
vi.mock("@/lib/api/users", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/users")>()),
  getPreferences: (...a: unknown[]) => api.getPreferences(...a),
  savePreferences: (...a: unknown[]) => api.savePreferences(...a),
  getUserStatus: (...a: unknown[]) => api.getUserStatus(...a),
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

const LABEL = "Add sessions that show a client’s full name"

describe("Settings → booking sessions by full name", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.savePreferences.mockImplementation((prefs: unknown) => Promise.resolve(prefs))
  })

  it("shows the server's default until the clinician chooses", async () => {
    api.getPreferences.mockResolvedValue(PREFERENCES)
    api.getUserStatus.mockResolvedValue({ books_sessions_named_in_title: false })
    renderWithProviders(<NameBookingSetting />)

    const box = await screen.findByRole("checkbox", { name: LABEL })
    expect(box).not.toBeChecked()
    expect(
      screen.getByText("Otherwise Pablo asks first, with the client already chosen."),
    ).toBeInTheDocument()
  })

  it("turns it on and saves the choice", async () => {
    api.getPreferences.mockResolvedValue(PREFERENCES)
    api.getUserStatus.mockResolvedValue({ books_sessions_named_in_title: false })
    renderWithProviders(<NameBookingSetting />)

    await userEvent.click(await screen.findByRole("checkbox", { name: LABEL }))

    await waitFor(() =>
      expect(api.savePreferences).toHaveBeenCalledWith(
        { ...PREFERENCES, book_sessions_named_in_title: true },
        undefined,
      ),
    )
    await waitFor(() => expect(screen.getByRole("checkbox", { name: LABEL })).toBeChecked())
  })

  it("shows a choice already made over the default, and turns it off", async () => {
    api.getPreferences.mockResolvedValue({ ...PREFERENCES, book_sessions_named_in_title: true })
    api.getUserStatus.mockResolvedValue({ books_sessions_named_in_title: false })
    renderWithProviders(<NameBookingSetting />)

    const box = await screen.findByRole("checkbox", { name: LABEL })
    expect(box).toBeChecked()
    await userEvent.click(box)

    await waitFor(() =>
      expect(api.savePreferences).toHaveBeenCalledWith(
        { ...PREFERENCES, book_sessions_named_in_title: false },
        undefined,
      ),
    )
  })
})
