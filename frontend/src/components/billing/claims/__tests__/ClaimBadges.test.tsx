// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The state badge for every state, and the deadline badge at each threshold.
 *
 * The one rule with teeth: "Sent" never appears for a claim that has not
 * left the practice. A `validated` claim is queued, not sent.
 */

import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"
import { ClaimStateBadge, DeadlineBadge } from "../ClaimBadges"
import type { ClaimDeadlines, ClaimState } from "@/types/claims"

const EXPECTED_LABELS: Record<ClaimState, string> = {
  draft: "Draft",
  validated: "Queued to send",
  submitted: "Sent",
  ch_accepted: "Accepted by clearinghouse",
  payer_accepted: "Accepted by payer",
  paid: "Paid",
  partial: "Partially paid",
  denied: "Denied",
  rejected: "Rejected",
  stalled: "Needs attention",
}

describe("ClaimStateBadge", () => {
  it.each(Object.entries(EXPECTED_LABELS) as [ClaimState, string][])(
    "renders %s as %s",
    (state, label) => {
      render(<ClaimStateBadge state={state} />)
      const badge = screen.getByTestId("claim-state")
      expect(badge).toHaveTextContent(label)
      expect(badge).toHaveAttribute("data-state", state)
    },
  )

  it("never says Sent for a validated claim", () => {
    render(<ClaimStateBadge state="validated" />)
    expect(screen.getByTestId("claim-state").textContent).not.toMatch(/sent/i)
  })

  it("never says Sent for a draft", () => {
    render(<ClaimStateBadge state="draft" />)
    expect(screen.getByTestId("claim-state").textContent).not.toMatch(/sent/i)
  })
})

function filing(daysLeft: number): ClaimDeadlines {
  return {
    filing: "2026-09-20",
    correction: null,
    appeal: null,
    applicable: "filing",
    days_left: daysLeft,
  }
}

describe("DeadlineBadge", () => {
  it("renders nothing for a claim under no clock", () => {
    render(
      <DeadlineBadge
        deadlines={{ filing: null, correction: null, appeal: null, applicable: null, days_left: null }}
        state="paid"
      />,
    )
    expect(screen.queryByTestId("claim-deadline")).not.toBeInTheDocument()
  })

  it("is plain with more than 14 days left", () => {
    render(<DeadlineBadge deadlines={filing(15)} state="validated" />)
    const badge = screen.getByTestId("claim-deadline")
    expect(badge).toHaveAttribute("data-tone", "neutral")
    expect(badge).toHaveTextContent("Filing closes Sep 20, 2026 (15 days)")
  })

  it("turns amber at 14 days", () => {
    render(<DeadlineBadge deadlines={filing(14)} state="validated" />)
    expect(screen.getByTestId("claim-deadline")).toHaveAttribute("data-tone", "warning")
  })

  it("stays amber at 3 days", () => {
    render(<DeadlineBadge deadlines={filing(3)} state="validated" />)
    expect(screen.getByTestId("claim-deadline")).toHaveAttribute("data-tone", "warning")
  })

  it("turns red at 2 days", () => {
    render(<DeadlineBadge deadlines={filing(2)} state="validated" />)
    expect(screen.getByTestId("claim-deadline")).toHaveAttribute("data-tone", "danger")
  })

  it("is red on the day", () => {
    render(<DeadlineBadge deadlines={filing(0)} state="validated" />)
    const badge = screen.getByTestId("claim-deadline")
    expect(badge).toHaveAttribute("data-tone", "danger")
    expect(badge).toHaveTextContent("(0 days)")
  })

  it("is red and says so once the deadline has passed", () => {
    render(<DeadlineBadge deadlines={filing(-3)} state="validated" />)
    const badge = screen.getByTestId("claim-deadline")
    expect(badge).toHaveAttribute("data-tone", "danger")
    expect(badge).toHaveTextContent("passed 3 days ago")
  })

  it("tells a rejected claim to fix and refile before the date", () => {
    render(<DeadlineBadge deadlines={filing(10)} state="rejected" />)
    const badge = screen.getByTestId("claim-deadline")
    expect(badge).toHaveTextContent("Fix and refile before Sep 20, 2026 (10 days)")
    expect(badge).toHaveAttribute("data-tone", "warning")
  })

  it("names the appeal window when that is the clock that binds", () => {
    render(
      <DeadlineBadge
        deadlines={{
          filing: null,
          correction: "2026-10-15",
          appeal: "2026-10-01",
          applicable: "appeal",
          days_left: 25,
        }}
        state="denied"
      />,
    )
    expect(screen.getByTestId("claim-deadline")).toHaveTextContent(
      "Appeal window closes Oct 1, 2026 (25 days)",
    )
  })
})
