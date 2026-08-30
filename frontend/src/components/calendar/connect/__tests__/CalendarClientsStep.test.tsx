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

  it("fires onScan from the Look at my week button", async () => {
    const user = userEvent.setup()
    const onScan = vi.fn()
    render(
      <CalendarClientsStep
        busyWindows={GRANTED}
        proposal={null}
        scanning={false}
        error={null}
        onScan={onScan}
        onSkip={vi.fn()}
      />
    )

    await user.click(screen.getByRole("button", { name: /look at my week/i }))
    expect(onScan).toHaveBeenCalledOnce()
  })

  it("fires onSkip from the skip button, without scanning first", async () => {
    const user = userEvent.setup()
    const onSkip = vi.fn()
    render(
      <CalendarClientsStep
        busyWindows={GRANTED}
        proposal={null}
        scanning={false}
        error={null}
        onScan={vi.fn()}
        onSkip={onSkip}
      />
    )

    await user.click(screen.getByRole("button", { name: /skip, i.ll add them myself/i }))
    expect(onSkip).toHaveBeenCalledOnce()
  })

  it("renders the exact fought-over title, lede, and button copy", () => {
    render(
      <CalendarClientsStep
        busyWindows={GRANTED}
        proposal={null}
        scanning={false}
        error={null}
        onScan={vi.fn()}
        onSkip={vi.fn()}
      />
    )

    expect(screen.getByText("Bring over your week")).toBeInTheDocument()
    expect(
      screen.getByText(
        "Pablo looks at the rhythm of your calendar - events that repeat weekly or every other week, the way sessions do. It can't tell a client from a standing meeting, so nothing is added until you say so."
      )
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Look at my week" })).toBeInTheDocument()
  })

  it("renders left_alone matching the scan response", () => {
    render(
      <CalendarClientsStep
        busyWindows={GRANTED}
        proposal={proposal({ left_alone: 9 })}
        scanning={false}
        error={null}
        onScan={vi.fn()}
        onSkip={vi.fn()}
      />
    )

    expect(screen.getByTestId("left-alone-count")).toHaveTextContent("9")
  })

  it("never asserts a category the heuristic can't verify", () => {
    const { container } = render(
      <CalendarClientsStep
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
