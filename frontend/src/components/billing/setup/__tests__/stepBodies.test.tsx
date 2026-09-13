// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Every step a therapist can reach has a real screen behind it.
 *
 * The type system already guarantees a body EXISTS for each `StepId` — the
 * table is `Record<StepId, ...>`. What it cannot say is whether that body is a
 * screen or a placeholder, and for months two routes compiled perfectly while
 * dead-ending on "This step isn't built yet": the record step, and then again
 * at their ending.
 *
 * So this walks the ids each combination of answers actually reaches and
 * asserts none of them renders the not-built panel. A future step added as a
 * stub is then a failing test rather than something a therapist finds by
 * walking the flow.
 *
 * The endings carry a second bug class of their own, and it is the sharper
 * one: a finish screen that claims something we cannot deliver. Saying "we'll
 * put your applications in" before she has authorised anything, or that she can
 * bill insurance because setup finished, both send her to act on something that
 * is not true.
 */

import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { type CurrentStateId, stepsForState } from "../routes"
import { STEP_BODIES } from "../stepBodies"

vi.mock("@/components/settings/PracticeIdentityCard", () => ({
  PracticeIdentityCard: () => <div />,
}))
vi.mock("@/components/settings/BillingContactCard", () => ({
  BillingContactCard: () => <div />,
}))
vi.mock("@/components/settings/AppointmentTypesCard", () => ({
  AppointmentTypesCard: () => <div />,
}))
vi.mock("@/components/settings/PayersCard", () => ({ PayersCard: () => <div /> }))
vi.mock("@/components/credentialing/CredentialingWizard", () => ({
  CredentialingWizard: () => <div>credentialing checklist</div>,
}))
vi.mock("@/components/credentialing/NpiLookupStep", () => ({
  NpiLookupStep: () => <div />,
}))
vi.mock("@/hooks/useBillingProfile", () => ({
  useBillingProfile: () => ({ data: undefined, isLoading: false }),
}))

const NOT_BUILT = /isn.t built yet/i

const ALL: CurrentStateId[] = ["self_pay", "platform", "own_insurance"]

function everySubset(): CurrentStateId[][] {
  const out: CurrentStateId[][] = []
  for (let mask = 0; mask < 1 << ALL.length; mask++) {
    out.push(ALL.filter((_, i) => mask & (1 << i)))
  }
  return out
}

const CASES = everySubset().flatMap((state) =>
  [false, true].map(
    (wants) =>
      [`${state.join("+") || "(none)"}${wants ? " +credentialing" : ""}`, state, wants] as const,
  ),
)

function noop() {}

function props(selected: CurrentStateId[], wantsCredentialing: boolean) {
  return {
    selected,
    wantsCredentialing,
    onToggle: noop,
    onToggleCredentialing: noop,
    onContinue: noop,
    onBack: noop,
    onNoClients: noop,
  }
}

describe("every reachable step has a real screen", () => {
  it.each(CASES)("%s renders no placeholder on any step", (_label, state, wants) => {
    for (const step of stepsForState(state, wants)) {
      const Body = STEP_BODIES[step.id]
      const { unmount } = render(<Body {...props(state, wants)} />)
      expect(
        screen.queryByText(NOT_BUILT),
        `${_label} → ${step.id} is still a placeholder`,
      ).not.toBeInTheDocument()
      unmount()
    }
  })

  it("shows the credentialing checklist on the record step", () => {
    // Mounted, not rebuilt — the same component Settings uses, so the two
    // cannot drift into disagreeing about one record.
    const Body = STEP_BODIES.record
    render(<Body {...props(["self_pay"], true)} />)

    expect(screen.getByText("credentialing checklist")).toBeInTheDocument()
  })
})

describe("what an ending may not claim", () => {
  it("does not tell an unpanelled clinician she is set up to bill", () => {
    // She has recorded her facts and applied to nobody. Saying otherwise would
    // send her looking for claims that cannot exist yet.
    const Body = STEP_BODIES.done
    render(<Body {...props(["self_pay"], true)} />)

    expect(screen.queryByText(/set up to bill/i)).not.toBeInTheDocument()
  })

  it("does not promise to submit applications, only to prepare and track them", () => {
    // Pablo cannot sign her name to a payer's form until she has authorised
    // it, so an ending that says the applications are going in is writing a
    // cheque the authorisation layer has not signed.
    const Body = STEP_BODIES.done
    render(<Body {...props(["platform"], true)} />)

    expect(screen.getByText(/prepares and tracks/i)).toBeInTheDocument()
    expect(screen.queryByText(/put your applications in/i)).not.toBeInTheDocument()
  })

  it("separates being contracted from being able to file a claim", () => {
    const Body = STEP_BODIES.done
    render(<Body {...props(["platform"], true)} />)

    expect(screen.getByText(/different things/i)).toBeInTheDocument()
  })

  it("tells a platform clinician her current billing is untouched", () => {
    // The commonest fear about pointing a second system at your billing is
    // that it quietly starts moving money.
    const Body = STEP_BODIES.done
    render(<Body {...props(["platform"], true)} />)

    expect(screen.getByText(/changes how you.re billed today/i)).toBeInTheDocument()
  })

  it("never claims her record back from the platform", () => {
    // "Your record is yours, whatever the platform holds" was strong and
    // wrong: it implies we hold, retrieved, or can separate something that
    // lives in somebody else's system.
    const Body = STEP_BODIES.done
    for (const wants of [false, true]) {
      const { unmount } = render(<Body {...props(["platform"], wants)} />)
      expect(screen.queryByText(/whatever the platform holds/i)).not.toBeInTheDocument()
      unmount()
    }
  })

  it("points a platform clinician at her own agreement without reading it for her", () => {
    const Body = STEP_BODIES.done
    render(<Body {...props(["self_pay", "platform"], false)} />)

    expect(screen.getByTestId("platform-agreement-note")).toBeInTheDocument()
  })
})

describe("the plan screen", () => {
  it("leaves the credentialing ask unticked", () => {
    // Free or not, a pre-ticked box claims she asked for something she did
    // not.
    const Body = STEP_BODIES.plan
    render(<Body {...props(["platform"], false)} />)

    expect(screen.getByTestId("wants-credentialing")).not.toBeChecked()
  })

  it("reassures a platform clinician that nothing is being rewired", () => {
    const Body = STEP_BODIES.plan
    render(<Body {...props(["platform"], false)} />)

    expect(screen.getByTestId("platform-untouched")).toBeInTheDocument()
  })

  it("says nothing about platforms to someone who is not on one", () => {
    const Body = STEP_BODIES.plan
    render(<Body {...props(["self_pay"], false)} />)

    expect(screen.queryByTestId("platform-untouched")).not.toBeInTheDocument()
  })
})
