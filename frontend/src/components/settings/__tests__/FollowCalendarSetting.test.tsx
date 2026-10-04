// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { FollowCalendarSetting } from "../FollowCalendarSetting"

const { setFollowed, listCalendars, scan } = vi.hoisted(() => ({
  setFollowed: vi.fn(),
  listCalendars: vi.fn(),
  scan: vi.fn(),
}))

vi.mock("@/lib/api/outsideSessions", () => ({
  setFollowedCalendar: setFollowed,
  listFollowableCalendars: listCalendars,
}))
vi.mock("@/lib/api/scheduling", () => ({
  scanCalendarForImport: scan,
  importNeedsConsent: (result: { needs_consent?: boolean }) => result.needs_consent === true,
}))
vi.mock("@/components/calendar/connect/CalendarSetupWizard", () => ({
  CALENDAR_SETUP_PATH: "/dashboard/settings/calendar",
}))

const MAIN = "clinician@example.test"
const TEAM = "team@group.calendar.google.test"

beforeEach(() => {
  setFollowed.mockReset().mockResolvedValue({ follow_calendar_id: MAIN })
  listCalendars.mockReset().mockResolvedValue({
    calendars: [
      { id: MAIN, name: MAIN, primary: true },
      { id: TEAM, name: "Group practice", primary: false },
    ],
    follow_calendar_id: null,
  })
  scan.mockReset()
  window.sessionStorage.clear()
})

describe("FollowCalendarSetting", () => {
  it("turns following on for the main calendar when the calendar can be read", async () => {
    const user = userEvent.setup()
    const onChanged = vi.fn()
    render(
      <FollowCalendarSetting followedCalendarId={null} importGranted onChanged={onChanged} />
    )

    await user.click(screen.getByRole("checkbox", { name: "Keep importing new sessions" }))

    expect(setFollowed).toHaveBeenCalledWith("primary")
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it("lists the readable calendars, main first, and names the one it reads", async () => {
    listCalendars.mockResolvedValue({
      calendars: [
        { id: MAIN, name: MAIN, primary: true },
        { id: TEAM, name: "Group practice", primary: false },
      ],
      follow_calendar_id: MAIN,
    })
    render(<FollowCalendarSetting followedCalendarId="primary" importGranted onChanged={vi.fn()} />)

    const picker = await screen.findByRole("combobox", {
      name: "Import sessions from",
    })
    expect(Array.from((picker as HTMLSelectElement).options).map((o) => o.value)).toEqual([
      MAIN,
      TEAM,
    ])
    expect(picker).toHaveValue(MAIN)
    expect(screen.getByTestId("followed-calendar-line")).toHaveTextContent(
      `Pablo reads the events on ${MAIN} and asks about the ones that look like sessions.`
    )
  })

  it("says it books by full name when that setting is on", async () => {
    listCalendars.mockResolvedValue({
      calendars: [{ id: MAIN, name: MAIN, primary: true }],
      follow_calendar_id: MAIN,
    })
    render(
      <FollowCalendarSetting
        followedCalendarId="primary"
        importGranted
        booksNamedSessions
        onChanged={vi.fn()}
      />
    )

    expect(await screen.findByTestId("followed-calendar-line")).toHaveTextContent(
      `Pablo reads the events on ${MAIN}. It books the ones titled with a client’s full name, and asks about others that look like sessions.`
    )
  })

  it("shows which calendar it reads even when there is only one", async () => {
    listCalendars.mockResolvedValue({
      calendars: [{ id: MAIN, name: MAIN, primary: true }],
      follow_calendar_id: MAIN,
    })
    render(<FollowCalendarSetting followedCalendarId="primary" importGranted onChanged={vi.fn()} />)

    const picker = await screen.findByRole("combobox", { name: "Import sessions from" })
    expect(picker).toHaveValue(MAIN)
    expect(screen.getByTestId("followed-calendar-line")).toHaveTextContent(
      `Pablo reads the events on ${MAIN}`
    )
  })

  it("shows no picker while nothing is followed", () => {
    render(<FollowCalendarSetting followedCalendarId={null} importGranted onChanged={vi.fn()} />)

    expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
  })

  it("follows another calendar when one is picked", async () => {
    const user = userEvent.setup()
    listCalendars.mockResolvedValue({
      calendars: [
        { id: MAIN, name: MAIN, primary: true },
        { id: TEAM, name: "Group practice", primary: false },
      ],
      follow_calendar_id: MAIN,
    })
    render(<FollowCalendarSetting followedCalendarId={MAIN} importGranted onChanged={vi.fn()} />)

    await user.selectOptions(
      await screen.findByRole("combobox", { name: "Import sessions from" }),
      TEAM
    )

    expect(setFollowed).toHaveBeenCalledWith(TEAM)
  })

  it("stops following when unticked", async () => {
    const user = userEvent.setup()
    render(<FollowCalendarSetting followedCalendarId={MAIN} importGranted onChanged={vi.fn()} />)

    await user.click(screen.getByRole("checkbox", { name: "Keep importing new sessions" }))

    expect(setFollowed).toHaveBeenCalledWith(null)
  })

  it("without read access, can't be turned on and offers that access instead", async () => {
    const user = userEvent.setup()
    const assign = vi.fn()
    vi.stubGlobal("location", { ...window.location, origin: "https://app.test", assign })
    scan.mockResolvedValue({ needs_consent: true, capability: "import", auth_url: "https://g/auth" })
    render(
      <FollowCalendarSetting followedCalendarId={null} importGranted={false} onChanged={vi.fn()} />
    )

    expect(screen.getByRole("checkbox", { name: "Keep importing new sessions" })).toBeDisabled()
    expect(screen.getByText("This needs “Scan calendar” access.")).toBeInTheDocument()
    expect(listCalendars).not.toHaveBeenCalled()

    await user.click(screen.getByRole("button", { name: "Allow access" }))

    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://g/auth"))
    expect(scan.mock.calls[0][0]).toBe("https://app.test/dashboard/settings/calendar")
    expect(window.sessionStorage.getItem("pablo.calendar-import.pending")).toBe("1")
    expect(window.sessionStorage.getItem("pablo.calendar-follow.wanted")).toBe("1")
    expect(setFollowed).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })

  it("says so when the followed calendar can't be read any more", async () => {
    listCalendars.mockResolvedValue({
      calendars: [
        { id: MAIN, name: MAIN, primary: true },
        { id: TEAM, name: "Group practice", primary: false },
      ],
      follow_calendar_id: "gone@group.calendar.google.test",
    })
    render(
      <FollowCalendarSetting
        followedCalendarId="gone@group.calendar.google.test"
        importGranted
        onChanged={vi.fn()}
      />
    )

    expect(await screen.findByTestId("followed-calendar-unreadable")).toHaveTextContent(
      "Pablo can’t read the calendar it was following any more. Choose another."
    )
    expect(screen.queryByTestId("followed-calendar-line")).not.toBeInTheDocument()
    expect(
      screen.getByRole("combobox", { name: "Import sessions from" })
    ).not.toHaveValue(MAIN)
  })

  it("shows a pick at once, without going back to the old calendar", async () => {
    const user = userEvent.setup()
    listCalendars.mockResolvedValue({
      calendars: [
        { id: MAIN, name: MAIN, primary: true },
        { id: TEAM, name: "Group practice", primary: false },
      ],
      follow_calendar_id: MAIN,
    })
    setFollowed.mockResolvedValue({ follow_calendar_id: TEAM })
    render(<FollowCalendarSetting followedCalendarId={MAIN} importGranted onChanged={vi.fn()} />)
    const picker = await screen.findByRole("combobox", {
      name: "Import sessions from",
    })

    await user.selectOptions(picker, TEAM)

    await waitFor(() => expect(picker).toHaveValue(TEAM))
  })

  it("doesn't load calendars while nothing is followed", () => {
    render(<FollowCalendarSetting followedCalendarId={null} importGranted onChanged={vi.fn()} />)

    expect(listCalendars).not.toHaveBeenCalled()
    expect(screen.queryByText("Loading your calendars…")).not.toBeInTheDocument()
  })
})
