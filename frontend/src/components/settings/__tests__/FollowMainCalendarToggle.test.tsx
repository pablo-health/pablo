// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { FollowMainCalendarToggle } from "../FollowMainCalendarToggle"

const { setFollow, scan } = vi.hoisted(() => ({ setFollow: vi.fn(), scan: vi.fn() }))

vi.mock("@/lib/api/outsideSessions", () => ({ setFollowMainCalendar: setFollow }))
vi.mock("@/lib/api/scheduling", () => ({
  scanCalendarForImport: scan,
  importNeedsConsent: (result: { needs_consent?: boolean }) => result.needs_consent === true,
}))
vi.mock("@/components/calendar/connect/CalendarSetupWizard", () => ({
  CALENDAR_SETUP_PATH: "/dashboard/settings/calendar",
}))

beforeEach(() => {
  setFollow.mockReset().mockResolvedValue({ follow_main_calendar: true })
  scan.mockReset()
  window.sessionStorage.clear()
})

describe("FollowMainCalendarToggle", () => {
  it("turns following on when the calendar can be read", async () => {
    const user = userEvent.setup()
    const onChanged = vi.fn()
    render(<FollowMainCalendarToggle following={false} importGranted onChanged={onChanged} />)

    await user.click(
      screen.getByRole("checkbox", { name: "Keep bringing in new sessions from this calendar" })
    )

    expect(setFollow).toHaveBeenCalledWith(true)
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it("without read access, can't be turned on and offers that access instead", async () => {
    const user = userEvent.setup()
    const assign = vi.fn()
    vi.stubGlobal("location", { ...window.location, origin: "https://app.test", assign })
    scan.mockResolvedValue({ needs_consent: true, capability: "import", auth_url: "https://g/auth" })
    render(<FollowMainCalendarToggle following={false} importGranted={false} onChanged={vi.fn()} />)

    expect(
      screen.getByRole("checkbox", { name: "Keep bringing in new sessions from this calendar" })
    ).toBeDisabled()
    expect(screen.getByText("This needs “Look at my week” access.")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Allow access" }))

    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://g/auth"))
    expect(scan.mock.calls[0][0]).toBe("https://app.test/dashboard/settings/calendar")
    expect(window.sessionStorage.getItem("pablo.calendar-import.pending")).toBe("1")
    expect(window.sessionStorage.getItem("pablo.calendar-follow.wanted")).toBe("1")
    expect(setFollow).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })
})
