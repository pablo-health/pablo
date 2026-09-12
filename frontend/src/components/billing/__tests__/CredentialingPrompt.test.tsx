// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The way into billing setup from the billing page.
 *
 * The behaviour worth guarding is the silence: it must not prompt a clinician
 * about something the record already answers, which is the promise the whole
 * intake rests on and the easiest one to break on a screen she sees every day.
 *
 * It deliberately asks nothing itself. The question lives on the wizard's
 * first screen, where the answer is also recorded.
 */

import { fireEvent, render, screen } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { IntakeField, IntakeSurface } from "@/types/credentialing"
import { CredentialingPrompt } from "../CredentialingPrompt"

const useIntake = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useCredentialingIntake", () => ({
  useIntake: (...args: unknown[]) => useIntake(...args),
}))

function panelsField(answered: boolean): IntakeField {
  return {
    key: "payer_participation",
    label: "Which payers are you already in network with?",
    section: "practice_locations",
    tier: "tier_1_claims_ready",
    kind: "collection",
    required: true,
    applies_to: "all",
    source: null,
    help_text: null,
    current_value: null,
    choices: [],
    answered,
  }
}

function surface(fields: IntakeField[]): IntakeSurface {
  return {
    supervised: false,
    prescriber: false,
    claims_ready: false,
    progress: [],
    fields,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  useIntake.mockReturnValue({ data: surface([panelsField(false)]) })
})

describe("CredentialingPrompt", () => {
  it("offers setup when the record has nothing on file", () => {
    render(<CredentialingPrompt />)

    expect(screen.getByText("Finish setting up how you get paid")).toBeInTheDocument()
  })

  it("says nothing once the record answers it", () => {
    // The promise the intake rests on: never ask for what we can already read.
    useIntake.mockReturnValue({ data: surface([panelsField(true)]) })

    const { container } = render(<CredentialingPrompt />)

    expect(container).toBeEmptyDOMElement()
  })

  it("says nothing while the answer is still unknown", () => {
    useIntake.mockReturnValue({ data: undefined })

    const { container } = render(<CredentialingPrompt />)

    expect(container).toBeEmptyDOMElement()
  })

  it("leads to the wizard rather than asking here", () => {
    // Two surfaces asking the same question in different words is how they
    // drift apart. The wizard asks it, and records the answer.
    render(<CredentialingPrompt />)

    expect(screen.getByRole("link", { name: "Set up billing" })).toHaveAttribute(
      "href",
      "/dashboard/billing/setup",
    )
    expect(screen.getAllByRole("link")).toHaveLength(1)
  })

  it("says why it matters, in terms of what she gets paid", () => {
    render(<CredentialingPrompt />)

    expect(screen.getByText(/claim we file or a superbill/i)).toBeInTheDocument()
  })

  it("can be put away for the visit without going through setup", () => {
    // She came here to bill. The card is an offer, not a gate.
    render(<CredentialingPrompt />)

    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }))

    expect(
      screen.queryByText("Finish setting up how you get paid"),
    ).not.toBeInTheDocument()
  })
})
