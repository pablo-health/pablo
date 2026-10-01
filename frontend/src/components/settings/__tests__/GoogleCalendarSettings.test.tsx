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
      "Pablo will stop using your Google Calendar and remove what it read from it.",
    )
    expect(dialog).toHaveTextContent(
      "Your sessions in Pablo stay, but changes made in Google won’t reach them.",
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
