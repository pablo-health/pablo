// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A connection Pablo can no longer read says so, with the way back: one line
 * and Reconnect. A reconnect asks again for what the connection held, and
 * finishing it reads the calendars at once.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, renderHook, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import { CalendarReadProblem, useFinishReconnect } from "../CalendarReadProblem"

const api = vi.hoisted(() => ({
  getStatus: vi.fn(),
  authUrl: vi.fn(),
  complete: vi.fn(),
  syncNow: vi.fn(),
}))

vi.mock("@/lib/api/scheduling", () => ({
  getGoogleCalendarStatus: () => api.getStatus(),
  getGoogleCalendarAuthUrl: (...args: unknown[]) => api.authUrl(...args),
  completeGoogleCalendarConnect: (...args: unknown[]) => api.complete(...args),
}))
vi.mock("@/lib/api/outsideSessions", () => ({
  syncCalendarsNow: () => api.syncNow(),
}))
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ user: { uid: "u1" }, loading: false }),
}))

const CONNECTED = {
  connected: true,
  calendar_id: "me@example.test",
  calendar_name: null,
  last_synced_at: "2026-01-02T15:42:00",
  write_target: "primary",
  busy: true,
  event_titling: "initials",
  titling_needs_attestation: false,
  follow_calendar_id: "primary",
  import_granted: true,
  read_error: null,
  reads_paused: false,
}

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

const assign = vi.fn()

beforeEach(() => {
  api.getStatus.mockReset().mockResolvedValue(CONNECTED)
  api.authUrl.mockReset().mockResolvedValue({ auth_url: "https://google.test/auth" })
  api.complete.mockReset().mockResolvedValue({ status: "connected" })
  api.syncNow.mockReset().mockResolvedValue({ google_synced: true })
  assign.mockReset()
  vi.stubGlobal("location", { ...window.location, assign, origin: "http://localhost:3000" })
  window.sessionStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("CalendarReadProblem", () => {
  it("shows nothing while the calendar is being read", async () => {
    render(<CalendarReadProblem />, { wrapper: wrapper() })

    await waitFor(() => expect(api.getStatus).toHaveBeenCalled())
    expect(screen.queryByTestId("calendar-read-problem")).not.toBeInTheDocument()
  })

  it("says Pablo can't read the calendar once the grant is gone", async () => {
    api.getStatus.mockResolvedValue({ ...CONNECTED, read_error: "access_revoked" })
    render(<CalendarReadProblem />, { wrapper: wrapper() })

    const line = await screen.findByTestId("calendar-read-problem")
    expect(line).toHaveTextContent(/^Pablo can’t read your Google Calendar\.Reconnect$/)
  })

  it("says so once scheduled reads have stopped, whatever the failure", async () => {
    api.getStatus.mockResolvedValue({ ...CONNECTED, read_error: "read_failed", reads_paused: true })
    render(<CalendarReadProblem />, { wrapper: wrapper() })

    expect(await screen.findByTestId("calendar-read-problem")).toBeInTheDocument()
  })

  it("stays quiet after one failed read that may clear on its own", async () => {
    api.getStatus.mockResolvedValue({ ...CONNECTED, read_error: "read_failed" })
    render(<CalendarReadProblem />, { wrapper: wrapper() })

    await waitFor(() => expect(api.getStatus).toHaveBeenCalled())
    expect(screen.queryByTestId("calendar-read-problem")).not.toBeInTheDocument()
  })

  it("points to choosing another calendar when the followed one is gone, not to Reconnect", async () => {
    api.getStatus.mockResolvedValue({ ...CONNECTED, read_error: "calendar_not_found" })
    render(<CalendarReadProblem />, { wrapper: wrapper() })

    const line = await screen.findByTestId("calendar-read-problem")
    expect(line).toHaveTextContent("Pablo can no longer read the calendar it was importing from.")
    expect(screen.getByRole("link", { name: "Choose another" })).toHaveAttribute(
      "href",
      "/dashboard/settings/calendars",
    )
    expect(screen.queryByRole("button", { name: "Reconnect" })).not.toBeInTheDocument()
  })

  it("leaves a gone calendar to the follow setting where it sits beside it", async () => {
    api.getStatus.mockResolvedValue({ ...CONNECTED, read_error: "calendar_not_found" })
    render(<CalendarReadProblem reconnectOnly />, { wrapper: wrapper() })

    await waitFor(() => expect(api.getStatus).toHaveBeenCalled())
    expect(screen.queryByTestId("calendar-read-problem")).not.toBeInTheDocument()
  })

  it("reconnects with what the connection held, back to the calendar", async () => {
    api.getStatus.mockResolvedValue({ ...CONNECTED, read_error: "access_revoked" })
    const user = userEvent.setup()
    render(<CalendarReadProblem />, { wrapper: wrapper() })

    await user.click(await screen.findByRole("button", { name: "Reconnect" }))

    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://google.test/auth"))
    expect(api.authUrl).toHaveBeenCalledWith("http://localhost:3000/dashboard/calendar", {
      write_target: "primary",
      busy: true,
      event_titling: "initials",
      read_events: true,
    })
  })
})

describe("useFinishReconnect", () => {
  it("exchanges the code, then reads the calendars", async () => {
    window.sessionStorage.setItem(
      "pablo.calendar-reconnect.selection",
      JSON.stringify({ write_target: "primary", busy: true, event_titling: "initials", read_events: true }),
    )
    vi.stubGlobal("location", {
      ...window.location,
      origin: "http://localhost:3000",
      search: "?code=abc&state=signed",
    })
    const replace = vi.spyOn(window.history, "replaceState").mockImplementation(() => {})

    const { result } = renderHook(() => useFinishReconnect(), { wrapper: wrapper() })

    await waitFor(() => expect(api.syncNow).toHaveBeenCalled())
    expect(api.complete).toHaveBeenCalledWith(
      "abc",
      "signed",
      "http://localhost:3000/dashboard/calendar",
      expect.objectContaining({ read_events: true }),
    )
    expect(replace).toHaveBeenCalledWith(null, "", "/dashboard/calendar")
    expect(result.current).toBeNull()
    replace.mockRestore()
  })

  it("says so, with Reconnect, when the exchange fails", async () => {
    api.getStatus.mockResolvedValue({ ...CONNECTED, read_error: "access_revoked" })
    api.complete.mockRejectedValue(new Error("invalid_grant"))
    window.sessionStorage.setItem(
      "pablo.calendar-reconnect.selection",
      JSON.stringify({ write_target: "primary", busy: true, event_titling: "initials", read_events: true }),
    )
    vi.stubGlobal("location", {
      ...window.location,
      origin: "http://localhost:3000",
      search: "?code=abc&state=signed",
    })
    const replace = vi.spyOn(window.history, "replaceState").mockImplementation(() => {})

    function Page() {
      return <CalendarReadProblem error={useFinishReconnect()} />
    }
    render(<Page />, { wrapper: wrapper() })

    const line = await screen.findByTestId("calendar-read-problem")
    await waitFor(() =>
      expect(line).toHaveTextContent("Could not finish reconnecting. Try again."),
    )
    expect(screen.getByRole("button", { name: "Reconnect" })).toBeInTheDocument()
    expect(api.syncNow).not.toHaveBeenCalled()
    replace.mockRestore()
  })

  it("says nothing when the read after a good exchange fails: the grant is saved", async () => {
    api.syncNow.mockRejectedValue(new Error("google down"))
    window.sessionStorage.setItem(
      "pablo.calendar-reconnect.selection",
      JSON.stringify({ write_target: "primary", busy: true, event_titling: "initials", read_events: true }),
    )
    vi.stubGlobal("location", {
      ...window.location,
      origin: "http://localhost:3000",
      search: "?code=abc&state=signed",
    })
    const replace = vi.spyOn(window.history, "replaceState").mockImplementation(() => {})

    const { result } = renderHook(() => useFinishReconnect(), { wrapper: wrapper() })

    await waitFor(() => expect(api.syncNow).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(result.current).toBeNull()
    replace.mockRestore()
  })

  it("leaves a code it did not start alone", async () => {
    vi.stubGlobal("location", { ...window.location, search: "?code=abc&state=signed" })

    renderHook(() => useFinishReconnect(), { wrapper: wrapper() })

    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(api.complete).not.toHaveBeenCalled()
  })
})
