// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ConfirmImportResult, ImportProposal, ProposedSeries } from "@/lib/api/scheduling"
import { CalendarReviewStep } from "../CalendarReviewStep"

function series(overrides: Partial<ProposedSeries> = {}): ProposedSeries {
  return {
    candidate_key: `key-${Math.random()}`,
    summary: "Jane Miller",
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

function proposal(series_: ProposedSeries[]): ImportProposal {
  return {
    series: series_,
    left_alone: 3,
    events_read: 40,
    partial: false,
    lookback_days: 90,
    horizon_days: 90,
    timezone: "UTC",
  }
}

function baseProps() {
  return {
    checked: {},
    onToggle: vi.fn(),
    expanded: false,
    onToggleExpanded: vi.fn(),
    onBack: vi.fn(),
    onReviewAgain: vi.fn(),
    onConfirm: vi.fn(),
    confirming: false,
    error: null,
    result: null as ConfirmImportResult | null,
    onFinish: vi.fn(),
  }
}

describe("CalendarReviewStep", () => {
  it("renders the exact fought-over title and lede, with the real total interpolated", () => {
    const list = [series({ candidate_key: "a" }), series({ candidate_key: "b" })]
    render(<CalendarReviewStep {...baseProps()} proposal={proposal(list)} />)

    expect(screen.getByText("Which of these are clients?")).toBeInTheDocument()
    expect(
      screen.getByText(
        "These 2 repeat on a weekly or biweekly rhythm. Check the ones that are clients. Uncheck standups, classes, and anything else that just happens to repeat."
      )
    ).toBeInTheDocument()
  })

  it("lists every proposed series, in the order the API returned them", () => {
    const list = [
      series({ candidate_key: "a", summary: "Amy" }),
      series({ candidate_key: "b", summary: "Ben" }),
      series({ candidate_key: "c", summary: "Cara" }),
    ]
    render(<CalendarReviewStep {...baseProps()} proposal={proposal(list)} />)

    const names = screen.getAllByText(/^(Amy|Ben|Cara)$/).map((n) => n.textContent)
    expect(names).toEqual(["Amy", "Ben", "Cara"])
  })

  it("pre-checks only what the API marked preselected, unchecking a looks_finished series", () => {
    const list = [
      series({ candidate_key: "keep", summary: "Kept", preselected: true }),
      series({
        candidate_key: "stale",
        summary: "Stale",
        preselected: false,
        status: "looks_finished",
      }),
    ]
    render(
      <CalendarReviewStep
        {...baseProps()}
        proposal={proposal(list)}
        checked={{ keep: true, stale: false }}
      />
    )

    const boxes = screen.getAllByRole("checkbox")
    expect(boxes[0]).toBeChecked()
    expect(boxes[1]).not.toBeChecked()
  })

  it("fires onToggle for the row's own candidate_key, excluding it from what's checked", async () => {
    const user = userEvent.setup()
    const onToggle = vi.fn()
    const list = [series({ candidate_key: "a", summary: "Amy" })]
    render(
      <CalendarReviewStep
        {...baseProps()}
        proposal={proposal(list)}
        checked={{ a: true }}
        onToggle={onToggle}
      />
    )

    await user.click(screen.getByRole("checkbox"))
    expect(onToggle).toHaveBeenCalledWith("a")
  })

  it("shows five rows and a disclosure for a longer proposal, revealing the rest on click", async () => {
    const user = userEvent.setup()
    const list = Array.from({ length: 8 }, (_, i) =>
      series({ candidate_key: `k${i}`, summary: `Client ${i}` })
    )
    const onToggleExpanded = vi.fn()
    render(
      <CalendarReviewStep {...baseProps()} proposal={proposal(list)} onToggleExpanded={onToggleExpanded} />
    )

    expect(screen.getAllByRole("checkbox")).toHaveLength(5)
    const disclosure = screen.getByRole("button", { name: /show the other 3/i })
    await user.click(disclosure)
    expect(onToggleExpanded).toHaveBeenCalledOnce()
  })

  it("does not filter any candidate out of what could be confirmed, even hidden behind the disclosure", () => {
    const list = Array.from({ length: 8 }, (_, i) =>
      series({ candidate_key: `k${i}`, summary: `Client ${i}` })
    )
    const allChecked = Object.fromEntries(list.map((s) => [s.candidate_key, true]))
    render(
      <CalendarReviewStep {...baseProps()} proposal={proposal(list)} checked={allChecked} expanded />
    )

    // Every candidate is rendered (and so reachable/checkable) once expanded.
    expect(screen.getAllByRole("checkbox")).toHaveLength(8)
    expect(screen.getByRole("button", { name: /add 8 clients/i })).toBeInTheDocument()
  })

  it('the primary action reads "Add N clients" and updates live with the checkboxes', () => {
    const list = [series({ candidate_key: "a" }), series({ candidate_key: "b" })]
    render(
      <CalendarReviewStep
        {...baseProps()}
        proposal={proposal(list)}
        checked={{ a: true, b: false }}
      />
    )

    expect(screen.getByRole("button", { name: "Add 1 client" })).toBeInTheDocument()
  })

  it("disables confirm when nothing is checked — no auto-import shortcut", () => {
    const list = [series({ candidate_key: "a" })]
    render(<CalendarReviewStep {...baseProps()} proposal={proposal(list)} checked={{ a: false }} />)

    expect(screen.getByRole("button", { name: /add 0 clients/i })).toBeDisabled()
  })

  it("fires onConfirm from the primary action", async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn()
    const list = [series({ candidate_key: "a" })]
    render(
      <CalendarReviewStep
        {...baseProps()}
        proposal={proposal(list)}
        checked={{ a: true }}
        onConfirm={onConfirm}
      />
    )

    await user.click(screen.getByRole("button", { name: /add 1 client/i }))
    expect(onConfirm).toHaveBeenCalledOnce()
  })

  it("shows the exact footer copy naming the miss case, and the kept-nothing sentence appears once", () => {
    const list = [series({ candidate_key: "a" })]
    const { container } = render(<CalendarReviewStep {...baseProps()} proposal={proposal(list)} />)

    expect(
      screen.getByText(
        "Pablo read your calendar once and kept nothing. If a client isn't in this list - someone you see monthly, or on a changing schedule - add them once you're in. It takes a minute."
      )
    ).toBeInTheDocument()

    const occurrences = (container.textContent ?? "").split("kept nothing").length - 1
    expect(occurrences).toBe(1)
  })

  it("never claims a category the heuristic can't verify", () => {
    const list = [series({ candidate_key: "a" })]
    const { container } = render(<CalendarReviewStep {...baseProps()} proposal={proposal(list)} />)

    const text = container.textContent ?? ""
    expect(text).not.toMatch(/your clients/i)
    expect(text).not.toMatch(/personal/i)
  })

  it("after confirming, names what was imported and that read access ended", () => {
    const result: ConfirmImportResult = {
      confirmed: [{ candidate_key: "a", patient_id: "p-1", appointments_created: 4 }],
      patients_created: 1,
      appointments_created: 4,
      skipped: [],
    }
    render(<CalendarReviewStep {...baseProps()} proposal={proposal([series()])} result={result} />)

    expect(screen.getByText(/1 client added/i)).toBeInTheDocument()
    expect(screen.getByText(/4 appointments scheduled ahead/i)).toBeInTheDocument()
    expect(screen.getByText(/read access ended/i)).toBeInTheDocument()
  })

  it("reports a skipped series honestly rather than staying silent", () => {
    const result: ConfirmImportResult = {
      confirmed: [],
      patients_created: 1,
      appointments_created: 0,
      skipped: ["a"],
    }
    render(<CalendarReviewStep {...baseProps()} proposal={proposal([series()])} result={result} />)

    expect(screen.getByText(/collided with something already booked/i)).toBeInTheDocument()
  })

  it("fires onFinish from the post-confirm summary", async () => {
    const user = userEvent.setup()
    const onFinish = vi.fn()
    const result: ConfirmImportResult = {
      confirmed: [],
      patients_created: 1,
      appointments_created: 2,
      skipped: [],
    }
    render(
      <CalendarReviewStep
        {...baseProps()}
        proposal={proposal([series()])}
        result={result}
        onFinish={onFinish}
      />
    )

    await user.click(screen.getByRole("button", { name: /go to my calendar/i }))
    expect(onFinish).toHaveBeenCalledOnce()
  })

  it("offers a way back to the week when jumped to before a scan", () => {
    render(<CalendarReviewStep {...baseProps()} proposal={null} />)

    expect(screen.getByRole("button", { name: /back to your week/i })).toBeInTheDocument()
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument()
  })
})
