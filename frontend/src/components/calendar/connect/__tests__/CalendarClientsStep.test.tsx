// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { BusyWindowsGranted, ImportProposal, ProposedSeries } from "@/lib/api/scheduling"
import { CalendarClientsStep } from "../CalendarClientsStep"

// A distinctive, obviously-clinical event summary. If this ever shows up in
// the grid's rendered markup — text, title, or aria-label — the "anonymous
// shapes" guarantee is broken.
const DISTINCTIVE_SUMMARY = "Zorbulax Quintwhistle — CBT check-in"

function series(overrides: Partial<ProposedSeries> = {}): ProposedSeries {
  return {
    candidate_key: "key-1",
    summary: DISTINCTIVE_SUMMARY,
    weekday: 0,
    local_start_time: "09:00",
    duration_minutes: 50,
    cadence: "weekly",
    occurrences_in_window: 8,
    occurrences_ahead: 4,
    first_future_start: "2026-09-07T09:00:00Z",
    last_seen: "2026-08-31T09:00:00Z",
    recurrence_rule: "RRULE:FREQ=WEEKLY",
    status: "active",
    confidence: 0.9,
    preselected: true,
    source_identifier: "series:rec-1",
    match: { patient: null, possible: [], suggested_patient_id: null },
    ...overrides,
  }
}

function proposal(overrides: Partial<ImportProposal> = {}): ImportProposal {
  return {
    series: [series()],
    left_alone: 3,
    events_read: 40,
    partial: false,
    lookback_days: 90,
    horizon_days: 90,
    timezone: "UTC",
    ...overrides,
  }
}

const GRANTED: BusyWindowsGranted = {
  windows: [{ start: "2026-08-31T09:00:00", end: "2026-08-31T10:00:00" }],
}

function setMatchMedia(matches: boolean) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  })
}

describe("CalendarClientsStep", () => {
  beforeEach(() => {
    setMatchMedia(false)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("renders the pre-scan grid from the busy grant, undifferentiated", () => {
    const { container } = render(
      <CalendarClientsStep
        step={4}
        busyWindows={GRANTED}
        proposal={null}
        scanning={false}
        error={null}
        onScan={vi.fn()}
        onSkip={vi.fn()}
      />
    )

    // One busy block, no sage/ghost distinction yet.
    const grid = screen.getByTestId("week-grid")
    expect(grid.querySelector(".bg-secondary-500")).not.toBeInTheDocument()
    expect(grid.querySelector(".bg-muted")).toBeInTheDocument()
  })

  it("carries no event summary anywhere in the grid, before or after a scan", () => {
    const { container, rerender } = render(
      <CalendarClientsStep
        step={4}
        busyWindows={GRANTED}
        proposal={null}
        scanning={false}
        error={null}
        onScan={vi.fn()}
        onSkip={vi.fn()}
      />
    )
    expect(container.textContent).not.toContain(DISTINCTIVE_SUMMARY)
    expect(container.innerHTML).not.toContain(DISTINCTIVE_SUMMARY)

    rerender(
      <CalendarClientsStep
        step={4}
        busyWindows={GRANTED}
        proposal={proposal()}
        scanning={false}
        error={null}
        onScan={vi.fn()}
        onSkip={vi.fn()}
      />
    )
    expect(container.textContent).not.toContain(DISTINCTIVE_SUMMARY)
    expect(container.innerHTML).not.toContain(DISTINCTIVE_SUMMARY)
    // No node in the grid carries the summary as a title or aria-label either.
    for (const node of Array.from(container.querySelectorAll("[title], [aria-label]"))) {
      expect(node.getAttribute("title")).not.toBe(DISTINCTIVE_SUMMARY)
      expect(node.getAttribute("aria-label")).not.toBe(DISTINCTIVE_SUMMARY)
    }
  })

  it("sorts qualifying and non-qualifying blocks into two visually distinct end states", () => {
    render(
      <CalendarClientsStep
        step={4}
        busyWindows={GRANTED}
        proposal={proposal()}
        scanning={false}
        error={null}
        onScan={vi.fn()}
        onSkip={vi.fn()}
      />
    )

    const sage = screen.getByTestId("week-grid").querySelector(".bg-secondary-500")
    expect(sage).toBeInTheDocument()
    expect(screen.getByTestId("qualifying-count")).toHaveTextContent("1")
  })

  it("under prefers-reduced-motion, transitions collapse: no duration, no delay", () => {
    setMatchMedia(true)
    render(
      <CalendarClientsStep
        step={4}
        busyWindows={GRANTED}
        proposal={proposal()}
        scanning={false}
        error={null}
        onScan={vi.fn()}
        onSkip={vi.fn()}
      />
    )

    const sage = screen.getByTestId("week-grid").querySelector(".bg-secondary-500") as HTMLElement
    expect(sage.style.transitionDuration).toBe("0ms")
    expect(sage.style.transitionDelay).toBe("0ms")
  })

  it("without prefers-reduced-motion, sage blocks are staggered", () => {
    const twoSeries = proposal({
      series: [
        series({ candidate_key: "a", weekday: 0, local_start_time: "09:00" }),
        series({ candidate_key: "b", weekday: 1, local_start_time: "10:00" }),
      ],
    })
    render(
      <CalendarClientsStep
        step={4}
        busyWindows={GRANTED}
        proposal={twoSeries}
        scanning={false}
        error={null}
        onScan={vi.fn()}
        onSkip={vi.fn()}
      />
    )

    const sageBlocks = Array.from(
      screen.getByTestId("week-grid").querySelectorAll(".bg-secondary-500")
    ) as HTMLElement[]
    expect(sageBlocks).toHaveLength(2)
    const delays = sageBlocks.map((el) => el.style.transitionDelay)
    expect(new Set(delays).size).toBe(2)
  })

  it("fires onScan from the Scan calendar button", async () => {
    const user = userEvent.setup()
    const onScan = vi.fn()
    render(
      <CalendarClientsStep
        step={4}
        busyWindows={GRANTED}
        proposal={null}
        scanning={false}
        error={null}
        onScan={onScan}
        onSkip={vi.fn()}
      />
    )

    await user.click(screen.getByRole("button", { name: "Scan calendar" }))
    expect(onScan).toHaveBeenCalledOnce()
  })

  it("fires onSkip from the skip button, without scanning first", async () => {
    const user = userEvent.setup()
    const onSkip = vi.fn()
    render(
      <CalendarClientsStep
        step={4}
        busyWindows={GRANTED}
        proposal={null}
        scanning={false}
        error={null}
        onScan={vi.fn()}
        onSkip={onSkip}
      />
    )

    await user.click(screen.getByRole("button", { name: "Skip import" }))
    expect(onSkip).toHaveBeenCalledOnce()
  })

  it("renders the exact fought-over title, lede, and button copy", () => {
    render(
      <CalendarClientsStep
        step={4}
        busyWindows={GRANTED}
        proposal={null}
        scanning={false}
        error={null}
        onScan={vi.fn()}
        onSkip={vi.fn()}
      />
    )

    expect(screen.getByText("Step 4 · Optional")).toBeInTheDocument()
    expect(screen.getByText("Import recurring sessions")).toBeInTheDocument()
    expect(
      screen.getByText(
        "Pablo can find events that repeat weekly or every other week. You'll choose which ones to import."
      )
    ).toBeInTheDocument()
    // Free/busy has no titles, so the preview calls them busy times.
    expect(screen.getByText("Busy times from a typical week.")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Scan calendar" })).toBeInTheDocument()
  })

  it("sorts the scanned week into possible sessions and other busy times", () => {
    render(
      <CalendarClientsStep
        step={4}
        busyWindows={GRANTED}
        proposal={proposal()}
        scanning={false}
        error={null}
        onScan={vi.fn()}
        onSkip={vi.fn()}
      />
    )

    expect(screen.getByTestId("qualifying-count").parentElement).toHaveTextContent(
      "1possible recurring session"
    )
    expect(screen.getByTestId("ghost-count").parentElement).toHaveTextContent(/other busy times?$/)
  })

  it("names the follow toggle for what it does, which includes one-off sessions", () => {
    render(
      <CalendarClientsStep
        step={4}
        busyWindows={GRANTED}
        proposal={proposal()}
        scanning={false}
        error={null}
        onScan={vi.fn()}
        onSkip={vi.fn()}
        onFollowingChange={vi.fn()}
      />
    )

    expect(
      screen.getByRole("checkbox", { name: /^Keep importing new sessions/ })
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        "Pablo books sessions whose title has a client\u2019s full name and asks about the rest."
      )
    ).toBeInTheDocument()
  })

  it("says it asks about each new session when names don't book", () => {
    render(
      <CalendarClientsStep
        step={4}
        busyWindows={GRANTED}
        proposal={proposal()}
        scanning={false}
        error={null}
        onScan={vi.fn()}
        onSkip={vi.fn()}
        onFollowingChange={vi.fn()}
        booksNamedSessions={false}
      />
    )

    expect(
      screen.getByText("Pablo asks who each new session is with and remembers your answer.")
    ).toBeInTheDocument()
  })

  describe("choosing the calendar to follow", () => {
    const CALENDARS = [
      { id: "me@example.test", name: "me@example.test", primary: true },
      { id: "booked@group.calendar.google.test", name: "Booked sessions", primary: false },
    ]

    it("shows the calendar it would import from and lets another be picked", async () => {
      const user = userEvent.setup()
      const onPick = vi.fn()
      render(
        <CalendarClientsStep
          step={4}
          busyWindows={GRANTED}
          proposal={proposal()}
          scanning={false}
          error={null}
          onScan={vi.fn()}
          onSkip={vi.fn()}
          onFollowingChange={vi.fn()}
          calendars={CALENDARS}
          followCalendarId="me@example.test"
          onFollowCalendarChange={onPick}
        />
      )

      expect(screen.getByRole("checkbox", { name: /^Keep importing new sessions/ })).toBeInTheDocument()
      const picker = screen.getByRole("combobox", { name: "From" })
      expect(picker).toHaveValue("me@example.test")
      expect(Array.from((picker as HTMLSelectElement).options).map((o) => o.text)).toEqual([
        "me@example.test",
        "Booked sessions",
      ])

      await user.selectOptions(picker, "booked@group.calendar.google.test")

      expect(onPick).toHaveBeenCalledWith("booked@group.calendar.google.test")
    })

    it("offers no choice with only one calendar", () => {
      render(
        <CalendarClientsStep
          step={4}
          busyWindows={GRANTED}
          proposal={proposal()}
          scanning={false}
          error={null}
          onScan={vi.fn()}
          onSkip={vi.fn()}
          onFollowingChange={vi.fn()}
          calendars={[CALENDARS[0]]}
          followCalendarId="me@example.test"
          onFollowCalendarChange={vi.fn()}
        />
      )

      expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
    })

    describe("a calendar Pablo made for another setup", () => {
      const WITH_ANOTHER = [
        ...CALENDARS,
        {
          id: "another@group.calendar.google.test",
          name: "Pablo Sessions",
          primary: false,
          made_by_pablo: true,
        },
      ]
      const renderStep = (onPick = vi.fn()) =>
        render(
          <CalendarClientsStep
            step={4}
            busyWindows={GRANTED}
            proposal={proposal()}
            scanning={false}
            error={null}
            onScan={vi.fn()}
            onSkip={vi.fn()}
            onFollowingChange={vi.fn()}
            calendars={WITH_ANOTHER}
            followCalendarId="me@example.test"
            onFollowCalendarChange={onPick}
          />
        )

      it("is listed with a flag", () => {
        renderStep()

        const options = Array.from(
          (screen.getByRole("combobox", { name: "From" }) as HTMLSelectElement).options
        ).map((o) => o.text)
        expect(options).toContain("Pablo Sessions (another Pablo setup)")
        expect(options).toContain("Booked sessions")
      })

      it("warns when picked, and is chosen only once confirmed", async () => {
        const user = userEvent.setup()
        const onPick = vi.fn()
        renderStep(onPick)

        await user.selectOptions(
          screen.getByRole("combobox", { name: "From" }),
          "another@group.calendar.google.test"
        )

        expect(screen.getByRole("alertdialog")).toHaveTextContent(
          "Pablo made this calendar for another setup. Importing from it brings in its upcoming sessions, including any it books from now on."
        )
        expect(onPick).not.toHaveBeenCalled()

        await user.click(screen.getByRole("button", { name: "Import from it" }))

        expect(onPick).toHaveBeenCalledWith("another@group.calendar.google.test")
        expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
      })

      it("is left unchosen when the warning is cancelled", async () => {
        const user = userEvent.setup()
        const onPick = vi.fn()
        renderStep(onPick)
        const picker = screen.getByRole("combobox", { name: "From" })

        await user.selectOptions(picker, "another@group.calendar.google.test")
        await user.click(screen.getByRole("button", { name: "Cancel" }))

        expect(onPick).not.toHaveBeenCalled()
        expect(picker).toHaveValue("me@example.test")
      })
    })

    it("is offered before any scan once the calendar can be read", () => {
      render(
        <CalendarClientsStep
          step={4}
          busyWindows={GRANTED}
          proposal={null}
          scanning={false}
          error={null}
          onScan={vi.fn()}
          onSkip={vi.fn()}
          onFollowingChange={vi.fn()}
          canFollow
          calendars={CALENDARS}
          followCalendarId="booked@group.calendar.google.test"
          onFollowCalendarChange={vi.fn()}
        />
      )

      expect(screen.getByRole("checkbox", { name: /^Keep importing new sessions/ })).toBeInTheDocument()
      expect(screen.getByRole("combobox", { name: "From" })).toHaveValue(
        "booked@group.calendar.google.test"
      )
      expect(screen.getByRole("button", { name: "Scan calendar" })).toBeInTheDocument()
    })

    it("isn't offered before a scan without read access", () => {
      render(
        <CalendarClientsStep
          step={4}
          busyWindows={GRANTED}
          proposal={null}
          scanning={false}
          error={null}
          onScan={vi.fn()}
          onSkip={vi.fn()}
          onFollowingChange={vi.fn()}
        />
      )

      expect(screen.queryByRole("checkbox")).not.toBeInTheDocument()
    })

    it("offers no import from the main calendar while following it", () => {
      render(
        <CalendarClientsStep
          step={4}
          busyWindows={GRANTED}
          proposal={null}
          scanning={false}
          error={null}
          onScan={vi.fn()}
          onSkip={vi.fn()}
          onFollowingChange={vi.fn()}
          canFollow
          following
          followingMain
          calendars={CALENDARS}
          followCalendarId="me@example.test"
          onFollowCalendarChange={vi.fn()}
        />
      )

      // Said once, in place of the step's own lede.
      expect(
        screen.getAllByText("Sessions on me@example.test already come in on their own.")
      ).toHaveLength(1)
      expect(screen.queryByText(/repeat weekly or every other week/)).not.toBeInTheDocument()
      expect(screen.queryByRole("button", { name: "Scan calendar" })).not.toBeInTheDocument()
      expect(screen.queryByRole("button", { name: "Skip import" })).not.toBeInTheDocument()
    })
  })

  it("never asserts a category the heuristic can't verify", () => {
    const { container } = render(
      <CalendarClientsStep
        step={4}
        busyWindows={GRANTED}
        proposal={proposal()}
        scanning={false}
        error={null}
        onScan={vi.fn()}
        onSkip={vi.fn()}
      />
    )

    const text = container.textContent ?? ""
    expect(text).not.toMatch(/your clients/i)
    expect(text).not.toMatch(/personal/i)
  })
})
