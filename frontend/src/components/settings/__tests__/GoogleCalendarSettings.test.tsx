// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Disconnecting Google Calendar from Settings asks first, and says what goes.
 *
 * A disconnect revokes Pablo's access and deletes what it read from the
 * calendar, so it never happens on the first click: the dialog has to be
 * confirmed, and cancelling it leaves the connection alone.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { GoogleCalendarSettings } from "../GoogleCalendarSettings"

const { getStatus, disconnect } = vi.hoisted(() => ({
  getStatus: vi.fn(),
  disconnect: vi.fn(),
}))

vi.mock("@/lib/api/scheduling", () => ({
  getGoogleCalendarStatus: () => getStatus(),
  disconnectGoogleCalendar: () => disconnect(),
}))
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ user: { uid: "u1" }, loading: false, getIdToken: async () => "token" }),
}))
// Its own card, tested in FollowCalendarSetting.test.tsx.
vi.mock("../FollowCalendarSetting", () => ({ FollowCalendarSetting: () => null }))
vi.mock("../NameBookingSetting", () => ({ useBooksSessionsNamedInTitle: () => false }))

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <GoogleCalendarSettings />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  getStatus.mockReset().mockResolvedValue({
    connected: true,
    calendar_id: "made@group.calendar.google.test",
    calendar_name: "Pablo Sessions",
    last_synced_at: null,
    write_target: "app_calendar",
    follow_calendar_id: null,
    import_granted: false,
  })
  disconnect.mockReset().mockResolvedValue({ status: "disconnected" })
})

async function openDialog() {
  const user = userEvent.setup()
  renderCard()
  await user.click(await screen.findByRole("button", { name: "Disconnect" }))
  const dialog = await screen.findByRole("dialog", { name: "Disconnect Google Calendar?" })
  return { user, dialog }
}

describe("GoogleCalendarSettings disconnect", () => {
  it("says what disconnecting removes and what stays, before doing anything", async () => {
    const { dialog } = await openDialog()

    expect(dialog).toHaveTextContent(
      "Pablo stops using your Google Calendar and deletes what it read from it.",
    )
    expect(dialog).toHaveTextContent(
      "Sessions already in Pablo stay, but changes in Google won’t reach them.",
    )
    expect(disconnect).not.toHaveBeenCalled()
  })

  it("disconnects once the dialog is confirmed", async () => {
    const { user, dialog } = await openDialog()

    await user.click(within(dialog).getByRole("button", { name: "Disconnect" }))

    await waitFor(() => expect(disconnect).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  })

  it("leaves the connection alone when cancelled", async () => {
    const { user, dialog } = await openDialog()

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }))

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
    expect(disconnect).not.toHaveBeenCalled()
  })

  it("says so when the disconnect fails", async () => {
    disconnect.mockRejectedValue(new Error("Could not reach Pablo."))
    const { user, dialog } = await openDialog()

    await user.click(within(dialog).getByRole("button", { name: "Disconnect" }))

    expect(await screen.findByText("Could not reach Pablo.")).toBeInTheDocument()
  })
})

describe("GoogleCalendarSettings reading the calendar", () => {
  it("shows when the calendar was last read, in 12-hour time", async () => {
    getStatus.mockResolvedValue({
      connected: true,
      calendar_id: "me@example.test",
      calendar_name: null,
      last_synced_at: "2026-01-02T15:42:00",
      write_target: "primary",
      follow_calendar_id: "primary",
      import_granted: true,
    })
    renderCard()

    expect(await screen.findByTestId("calendar-last-read")).toHaveTextContent(
      /^Last read Jan 2, 3:42 PM$/,
    )
    expect(screen.queryByText(/Last synced/)).not.toBeInTheDocument()
    expect(screen.queryByTestId("calendar-read-problem")).not.toBeInTheDocument()
  })

  it("says Pablo can't read the calendar, with Reconnect, once the grant is gone", async () => {
    getStatus.mockResolvedValue({
      connected: true,
      calendar_id: "me@example.test",
      calendar_name: null,
      last_synced_at: "2026-01-02T15:42:00",
      write_target: "primary",
      follow_calendar_id: "primary",
      import_granted: true,
      read_error: "access_revoked",
      reads_paused: false,
    })
    renderCard()

    const line = await screen.findByTestId("calendar-read-problem")
    expect(line).toHaveTextContent("Pablo can\u2019t read your Google Calendar.")
    expect(within(line).getByRole("button", { name: "Reconnect" })).toBeInTheDocument()
    // Still connected: the connection is there, it just can't be read.
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeInTheDocument()
  })
})

