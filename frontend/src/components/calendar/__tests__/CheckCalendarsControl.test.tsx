// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One control reads every calendar Pablo follows — Google and feeds — and
 * the time beside it says when they were last read.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { CheckCalendarsControl } from "../CheckCalendarsControl"
import { formatLastRead, oldestRead } from "../calendarReadTime"

const api = vi.hoisted(() => ({ getStatus: vi.fn(), syncNow: vi.fn() }))

vi.mock("@/lib/api/scheduling", () => ({
  getGoogleCalendarStatus: () => api.getStatus(),
}))
vi.mock("@/lib/api/outsideSessions", () => ({
  syncCalendarsNow: () => api.syncNow(),
}))
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ user: { uid: "u1" }, loading: false }),
}))

const FEED = {
  ehr_system: "simplepractice",
  connected: true,
  last_synced_at: "2026-01-02T15:30:00",
  last_sync_error: null,
}

function renderControl(feeds = [FEED], onRead = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, "invalidateQueries")
  render(
    <QueryClientProvider client={client}>
      <CheckCalendarsControl feeds={feeds} onRead={onRead} />
    </QueryClientProvider>,
  )
  return { invalidate, onRead }
}

beforeEach(() => {
  api.getStatus.mockReset().mockResolvedValue({
    connected: true,
    last_synced_at: "2026-01-02T15:42:00",
    follow_calendar_id: "primary",
  })
  api.syncNow.mockReset().mockResolvedValue({ google_synced: true, google_error: false, ical_errors: 0 })
})

describe("CheckCalendarsControl", () => {
  it("says when every calendar was last checked: the oldest of their reads", async () => {
    renderControl()

    expect(await screen.findByRole("button", { name: "Check calendars" })).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByTestId("calendar-last-read")).toHaveTextContent(
        /^Last checked Jan 2, 3:30 PM$/,
      ),
    )
  })

  it("reads Google and the feeds together, then refreshes the calendar", async () => {
    const user = userEvent.setup()
    const { invalidate, onRead } = renderControl()

    await user.click(await screen.findByRole("button", { name: "Check calendars" }))

    await waitFor(() => expect(onRead).toHaveBeenCalled())
    expect(api.syncNow).toHaveBeenCalledTimes(1)
    const keys = invalidate.mock.calls.map(([filters]) => filters?.queryKey)
    expect(keys).toContainEqual(["appointments"])
    expect(keys).toContainEqual(["google-calendar", "status"])
  })

  it("says so when the check fails", async () => {
    api.syncNow.mockRejectedValue(new Error("down"))
    const user = userEvent.setup()
    renderControl()

    await user.click(await screen.findByRole("button", { name: "Check calendars" }))

    expect(
      await screen.findByText("Could not check your calendars. Try again in a moment."),
    ).toBeInTheDocument()
  })

  it("says so when Google could not be read, though the check itself answered", async () => {
    api.syncNow.mockResolvedValue({ google_synced: false, google_error: true, ical_errors: 0 })
    const user = userEvent.setup()
    renderControl()

    await user.click(await screen.findByRole("button", { name: "Check calendars" }))

    expect(
      await screen.findByText("Could not check your calendars. Try again in a moment."),
    ).toBeInTheDocument()
    expect(screen.queryByTestId("calendar-last-read")).not.toBeInTheDocument()
  })

  it("says so when a feed could not be read", async () => {
    api.syncNow.mockResolvedValue({ google_synced: true, google_error: false, ical_errors: 1 })
    const user = userEvent.setup()
    renderControl()

    await user.click(await screen.findByRole("button", { name: "Check calendars" }))

    expect(
      await screen.findByText("Could not check your calendars. Try again in a moment."),
    ).toBeInTheDocument()
  })

  it("shows the time again after a check that read everything", async () => {
    api.syncNow.mockResolvedValue({ google_synced: true, google_error: false, ical_errors: 0 })
    const user = userEvent.setup()
    renderControl()

    await user.click(await screen.findByRole("button", { name: "Check calendars" }))

    await waitFor(() => expect(api.syncNow).toHaveBeenCalled())
    expect(await screen.findByTestId("calendar-last-read")).toHaveTextContent(/^Last checked /)
    expect(screen.queryByText(/Could not check/)).not.toBeInTheDocument()
  })

  it("is there for a Google calendar with no feeds", async () => {
    renderControl([])

    expect(await screen.findByRole("button", { name: "Check calendars" })).toBeInTheDocument()
  })

  it("is not there when nothing is connected", async () => {
    api.getStatus.mockResolvedValue({ connected: false, last_synced_at: null })
    renderControl([])

    await waitFor(() => expect(api.getStatus).toHaveBeenCalled())
    expect(screen.queryByRole("button", { name: "Check calendars" })).not.toBeInTheDocument()
  })
})

describe("formatLastRead", () => {
  const now = new Date("2026-01-02T18:00:00")

  it("is a 12-hour time for today", () => {
    expect(formatLastRead("2026-01-02T09:05:00", now)).toBe("9:05 AM")
    expect(formatLastRead("2026-01-02T15:42:00", now)).toBe("3:42 PM")
  })

  it("names the day for an earlier one", () => {
    expect(formatLastRead("2025-12-30T21:10:00", now)).toBe("Dec 30, 9:10 PM")
  })

  it("reads in the practice's zone when given", () => {
    expect(formatLastRead("2026-01-02T20:00:00Z", new Date("2026-01-02T21:00:00Z"), "America/New_York")).toBe(
      "3:00 PM",
    )
  })
})

describe("oldestRead", () => {
  it("is the oldest known read, or null", () => {
    expect(oldestRead(["2026-01-02T15:42:00Z", null, "2026-01-02T15:30:00Z"])).toBe("2026-01-02T15:30:00Z")
    expect(oldestRead([null, undefined])).toBeNull()
  })
})
