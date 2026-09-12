// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The panel question on the billing page.
 *
 * The behaviour worth guarding is the silence: it must not ask a clinician
 * something the record already answers, which is the promise the whole intake
 * rests on and the easiest one to break on a screen she sees every day.
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
  it("asks when the record has nothing on file", () => {
    render(<CredentialingPrompt />)

    expect(
      screen.getByText(/already contracted with insurance companies/i),
    ).toBeInTheDocument()
  })

  it("says nothing once she has told us", () => {
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

  it("sends an already-paneled clinician to record her payers, not merely away", () => {
    // The valuable half of the fork. Which payers she is contracted with is
    // what decides claim-versus-superbill on every session below this card.
    render(<CredentialingPrompt />)

    expect(
      screen.getByRole("link", { name: /tell us which ones/i }),
    ).toHaveAttribute("href", "/dashboard/settings/insurance")
  })

  it("sends a clinician who is not on panels to the intake", () => {
    render(<CredentialingPrompt />)

    expect(
      screen.getByRole("link", { name: /help me get on panels/i }),
    ).toHaveAttribute("href", "/dashboard/credentialing")
  })

  it("can be put away for the visit without answering", () => {
    // She came here to bill. The card is an offer, not a gate.
    render(<CredentialingPrompt />)

    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }))

    expect(
      screen.queryByText(/already contracted with insurance companies/i),
    ).not.toBeInTheDocument()
  })
})
