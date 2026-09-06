// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * EligibilityBadge tests — the one-line reading of the last check.
 *
 * The copy rule is the point: every state names the date the payer was
 * asked and none of them says "covered", because a 271 is what the payer
 * knew when asked, not a promise to pay.
 */

import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"

import { EligibilityBadge, carveoutText, eligibilityBadgeText } from "../EligibilityBadge"
import type { EligibilitySummary } from "@/types/coverage"

export function summary(overrides: Partial<EligibilitySummary> = {}): EligibilitySummary {
  return {
    status: "active",
    checked_at: "2026-09-06T15:00:00Z",
    payer_name: "UNITEDHEALTHCARE",
    plan_name: "Gold Plan HMO",
    plan_begin: "2024-01-01",
    copay_cents: null,
    coinsurance_pct: null,
    deductible_remaining_cents: 0,
    visit_limit: null,
    requires_authorization: null,
    carveout_administrator: null,
    aaa_errors: [],
    ...overrides,
  }
}

const CHECKED_ON = new Date("2026-09-06T15:00:00Z").toLocaleDateString()

describe("EligibilityBadge", () => {
  it("says the plan is active as of the day it was checked", () => {
    render(<EligibilityBadge summary={summary()} />)
    expect(screen.getByTestId("eligibility-badge")).toHaveTextContent(
      `Plan active as of ${CHECKED_ON}`,
    )
  })

  it("says the plan is inactive as of the day it was checked", () => {
    render(<EligibilityBadge summary={summary({ status: "inactive" })} />)
    expect(screen.getByTestId("eligibility-badge")).toHaveTextContent(
      `Plan inactive as of ${CHECKED_ON}`,
    )
  })

  it("says the payer could not confirm the plan on a refusal", () => {
    render(
      <EligibilityBadge
        summary={summary({
          status: "error",
          aaa_errors: [
            {
              code: "72",
              description: "Invalid/Missing Subscriber/Insured ID",
              followup_action: "Please Correct and Resubmit",
              resolution: null,
            },
          ],
        })}
      />,
    )
    expect(screen.getByTestId("eligibility-badge")).toHaveTextContent(
      "Payer could not confirm the plan",
    )
  })

  it("says the plan has not been checked when there is no answer yet", () => {
    render(<EligibilityBadge summary={null} />)
    expect(screen.getByTestId("eligibility-badge")).toHaveTextContent("Plan not yet checked")
  })

  it("never says covered, in any state", () => {
    const states: (EligibilitySummary | null)[] = [
      null,
      summary(),
      summary({ status: "inactive" }),
      summary({ status: "unknown" }),
      summary({ status: "error" }),
      summary({ carveout_administrator: { name: "EXAMPLE BEHAVIORAL HEALTH", payer_id: "X" } }),
    ]
    for (const state of states) {
      expect(eligibilityBadgeText(state).toLowerCase()).not.toContain("covered")
      expect((carveoutText(state) ?? "").toLowerCase()).not.toContain("covered")
    }
  })

  it("names the administrator and says to file there on a carve-out", () => {
    expect(
      carveoutText(
        summary({
          carveout_administrator: { name: "EXAMPLE BEHAVIORAL HEALTH", payer_id: "EXBH1" },
        }),
      ),
    ).toBe(
      "Behavioral benefits administered by EXAMPLE BEHAVIORAL HEALTH (payer ID EXBH1). File claims there.",
    )
    expect(carveoutText(summary())).toBeNull()
  })
})
