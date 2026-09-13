// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What she owes, and what we are carrying.
 *
 * Bug classes covered:
 *   * a payer's request buried under everything Pablo is already doing. The
 *     split into two sections is the product promise made legible, and a
 *     regression that merged them would look fine in a screenshot.
 *   * "nothing needs you" rendering as an empty box. Zero is the answer she is
 *     paying for and the screen has to say it out loud.
 *   * a failed fetch rendering as "nothing to do". The two are indistinguish-
 *     able to a reader and only one means she can stop thinking about it.
 *   * a raw status vocabulary reaching her. "in_review" is the payer's word
 *     and says nothing about who has it.
 *   * the screen re-sorting what the API ordered. Two opinions about what she
 *     should look at first is how the two drift.
 */

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, within } from "@testing-library/react"
import { PanelApplications } from "../PanelApplications"
import type { PanelApplication } from "@/types/credentialing"

const usePanelApplications = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useCredentialingChecklist", () => ({
  usePanelApplications: () => usePanelApplications(),
}))

function application(overrides: Partial<PanelApplication> = {}): PanelApplication {
  return {
    id: crypto.randomUUID(),
    payer_name: "Aetna",
    status: "in_review",
    action_owner: "pablo",
    awaiting: null,
    due_at: null,
    reference: null,
    submitted_at: null,
    effective_at: null,
    days_since_submitted: 30,
    ...overrides,
  }
}

function loaded(data: PanelApplication[], needsYou?: number) {
  usePanelApplications.mockReturnValue({
    data: {
      data,
      needs_you:
        needsYou ??
        data.filter(
          (app) =>
            app.action_owner === "therapist" &&
            app.status !== "effective" &&
            app.status !== "denied",
        ).length,
    },
    isLoading: false,
    isError: false,
  })
}

beforeEach(() => {
  usePanelApplications.mockReset()
})

describe("before the answer arrives", () => {
  it("says it is loading rather than that there is nothing", () => {
    usePanelApplications.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    })

    render(<PanelApplications />)

    expect(screen.getByText(/loading/i)).toBeInTheDocument()
  })

  it("a failure says so, instead of looking like nothing to do", () => {
    usePanelApplications.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    })

    render(<PanelApplications />)

    expect(screen.getByText(/couldn’t load your applications/i)).toBeInTheDocument()
    expect(screen.queryByText(/nothing needs you/i)).not.toBeInTheDocument()
  })
})

describe("a clinician who has not started", () => {
  it("gets nothing at all, so the start page stays a start page", () => {
    loaded([])

    const { container } = render(<PanelApplications />)

    expect(container).toBeEmptyDOMElement()
  })
})

describe("when nothing is hers to act on", () => {
  it("says so out loud rather than showing an empty section", () => {
    loaded([application({ payer_name: "Aetna" })])

    render(<PanelApplications />)

    expect(screen.getByText(/nothing needs you right now/i)).toBeInTheDocument()
    expect(screen.getByText(/only you can give/i)).toBeInTheDocument()
  })

  it("still shows what Pablo is carrying, so the silence is accounted for", () => {
    loaded([application({ payer_name: "Aetna" })])

    render(<PanelApplications />)

    expect(screen.getByText(/what pablo is carrying/i)).toBeInTheDocument()
    expect(screen.getByText("Aetna")).toBeInTheDocument()
  })
})

describe("when a payer needs something from her", () => {
  it("counts it in the heading, in words rather than a badge", () => {
    loaded([
      application({ payer_name: "Cigna", action_owner: "therapist" }),
      application({ payer_name: "Aetna" }),
    ])

    render(<PanelApplications />)

    expect(screen.getByText(/1 thing needs you/i)).toBeInTheDocument()
  })

  it("pluralises rather than saying '2 thing needs you'", () => {
    loaded([
      application({ payer_name: "Cigna", action_owner: "therapist" }),
      application({ payer_name: "United", action_owner: "therapist" }),
    ])

    render(<PanelApplications />)

    expect(screen.getByText(/2 things need you/i)).toBeInTheDocument()
  })

  it("keeps it out of the section Pablo is carrying", () => {
    loaded([
      application({ payer_name: "Cigna", action_owner: "therapist" }),
      application({ payer_name: "Aetna" }),
    ])

    render(<PanelApplications />)

    const ours = screen.getByText(/what pablo is carrying/i).closest("section")
    expect(within(ours as HTMLElement).queryByText("Cigna")).not.toBeInTheDocument()
    expect(within(ours as HTMLElement).getByText("Aetna")).toBeInTheDocument()
  })

  it("shows what it is waiting for in the words it was written in", () => {
    loaded([
      application({
        payer_name: "Cigna",
        action_owner: "therapist",
        status: "info_requested",
        awaiting: "Sign and return the W-9 they sent on the 3rd.",
      }),
    ])

    render(<PanelApplications />)

    expect(
      screen.getByText("Sign and return the W-9 they sent on the 3rd."),
    ).toBeInTheDocument()
  })

  it("leads the row with the deadline, not with how long it has waited", () => {
    loaded([
      application({
        payer_name: "Cigna",
        action_owner: "therapist",
        status: "info_requested",
        due_at: "2026-10-01T00:00:00Z",
        days_since_submitted: 60,
      }),
    ])

    render(<PanelApplications />)

    expect(screen.getByText(/needed by/i)).toBeInTheDocument()
    expect(screen.queryByText(/submitted/i)).not.toBeInTheDocument()
  })
})

describe("a settled application", () => {
  it("does not count as something she owes, even marked hers", () => {
    loaded(
      [
        application({
          payer_name: "Aetna",
          action_owner: "therapist",
          status: "effective",
          effective_at: "2026-08-01T00:00:00Z",
        }),
      ],
      0,
    )

    render(<PanelApplications />)

    expect(screen.getByText(/nothing needs you right now/i)).toBeInTheDocument()
    const ours = screen.getByText(/what pablo is carrying/i).closest("section")
    expect(within(ours as HTMLElement).getByText("Aetna")).toBeInTheDocument()
  })
})

describe("the words on a row", () => {
  it("never shows the payer's own status vocabulary", () => {
    loaded([application({ status: "in_review" })])

    render(<PanelApplications />)

    expect(screen.queryByText("in_review")).not.toBeInTheDocument()
    expect(screen.getByText(/with the payer/i)).toBeInTheDocument()
  })

  it("counts in weeks once days stop meaning anything", () => {
    loaded([application({ days_since_submitted: 63 })])

    render(<PanelApplications />)

    expect(screen.getByText(/submitted 9 weeks ago/i)).toBeInTheDocument()
  })

  it("counts in days while she is still counting days", () => {
    loaded([application({ days_since_submitted: 4 })])

    render(<PanelApplications />)

    expect(screen.getByText(/submitted 4 days ago/i)).toBeInTheDocument()
  })

  it("says an unfiled application is unfiled rather than filed today", () => {
    loaded([application({ status: "researching", days_since_submitted: null })])

    render(<PanelApplications />)

    expect(screen.getByText(/not submitted yet/i)).toBeInTheDocument()
  })
})

describe("the order", () => {
  it("is the API's, and the screen does not re-sort within a section", () => {
    loaded([
      application({ payer_name: "Second", action_owner: "pablo" }),
      application({ payer_name: "First", action_owner: "pablo" }),
    ])

    render(<PanelApplications />)

    const ours = screen.getByText(/what pablo is carrying/i).closest("section")
    const names = within(ours as HTMLElement)
      .getAllByRole("listitem")
      .map((item) => item.textContent)

    expect(names[0]).toContain("Second")
    expect(names[1]).toContain("First")
  })
})
