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
import { beforeEach, describe, expect, it, vi } from "vitest"

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
let profile: Record<string, unknown> | null = null
let clinician: Record<string, unknown> = { npi_number: null, taxonomy_code: null }
let paymentsConnected: boolean | null = null

vi.mock("../setupSlots.extensions", () => ({
  HAS_PAYMENTS_SETUP: true,
  PaymentsSetup: () => <div />,
  usePaymentsConnected: () => paymentsConnected,
}))

vi.mock("@/hooks/useBillingProfile", () => ({
  useBillingProfile: () => ({ data: profile, isLoading: false }),
}))

vi.mock("@/components/settings/useSettingsPreferences", () => ({
  useSettingsUserStatus: () => ({ data: clinician }),
}))

/** A profile with nothing a claim is refused for. */
function completeProfile() {
  return {
    legal_name: "Test Practice",
    tax_id_last4: "9714",
    tax_id_type: "ein",
    address_line1: "1 Test St",
    city: "Savannah",
    state: "GA",
    postal_code: "31401",
    phone: "9125550123",
    contact_email: "billing@example.com",
    billing_npi: "1234567893",
  }
}

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

function props(
  selected: CurrentStateId[],
  wantsCredentialing: boolean,
  wantsCardPayments = false,
) {
  return {
    selected,
    wantsCredentialing,
    wantsCardPayments,
    onToggle: noop,
    onToggleCredentialing: noop,
    onToggleCardPayments: noop,
    onContinue: noop,
    onBack: noop,
    onNoClients: noop,
  }
}

// The readiness mocks are module-level, so without this one test's incomplete
// profile becomes the next one's starting state — the same leak the wizard's
// own e2e hit, one layer down.
beforeEach(() => {
  profile = completeProfile()
  clinician = { npi_number: "1999999984", taxonomy_code: "101YM0800X" }
  // The base build's answer: no processor concept, so no claim either way.
  paymentsConnected = null
})

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

describe("an ending is honest about what is actually ready", () => {
  // Reaching the last step proves nothing: every step in this wizard can be
  // skipped, on purpose. So the completion wording has to ask what is on file
  // rather than assume that arriving here means finished. The worst sentence
  // on this screen is a readiness claim over an incomplete profile, because it
  // sends somebody off to see clients believing it.

  it("does not say a practice is set up to bill while the profile is incomplete", () => {
    profile = { legal_name: null, tax_id_last4: null, tax_id_type: null }
    const Body = STEP_BODIES.done
    render(<Body {...props(["own_insurance"], false)} />)

    expect(screen.queryByText("You're set up to bill")).not.toBeInTheDocument()
    expect(screen.getByText("Your billing setup is underway")).toBeInTheDocument()
  })

  it("says it is set up to bill once nothing is missing", () => {
    profile = completeProfile()
    clinician = { npi_number: "1999999984", taxonomy_code: "101YM0800X" }
    const Body = STEP_BODIES.done
    render(<Body {...props(["own_insurance"], false)} />)

    expect(screen.getByText("You're set up to bill")).toBeInTheDocument()
  })

  it("does not say direct payments are ready while the profile is incomplete", () => {
    profile = { legal_name: null, tax_id_last4: null }
    const Body = STEP_BODIES.done
    render(<Body {...props(["self_pay"], false)} />)

    expect(screen.getByText("Your direct-payment setup is saved")).toBeInTheDocument()
    expect(screen.getByTestId("setup-incomplete")).toBeInTheDocument()
  })

  it("does not say a card can be taken when no processor is connected", () => {
    // The first screen's "card, cash, bank transfer" says how the practice is
    // paid today. It is not a processor, and "ready to charge" read as one.
    profile = completeProfile()
    paymentsConnected = false
    const Body = STEP_BODIES.done
    render(<Body {...props(["self_pay"], false)} />)

    expect(screen.getByText(/connect a payment processor/i)).toBeInTheDocument()
    expect(screen.queryByText(/ready to charge/i)).not.toBeInTheDocument()
  })

  it("says it plainly once a processor is connected", () => {
    profile = completeProfile()
    paymentsConnected = true
    const Body = STEP_BODIES.done
    render(<Body {...props(["self_pay"], false)} />)

    expect(screen.getByText(/ready to charge/i)).toBeInTheDocument()
  })

  it("keeps the ordinary wording where there is no processor to ask about", () => {
    // `null` is a deployment that does not do processors at all, and the
    // moment before an implementation's read lands. Neither is grounds for
    // telling someone to go connect something.
    profile = completeProfile()
    paymentsConnected = null
    const Body = STEP_BODIES.done
    render(<Body {...props(["self_pay"], false)} />)

    expect(screen.getByText(/ready to charge/i)).toBeInTheDocument()
    expect(screen.queryByText(/connect a payment processor/i)).not.toBeInTheDocument()
  })

  it("does not promise a superbill without an NPI to put on it", () => {
    // A superbill carries the rendering provider's NPI and cannot be produced
    // without one. Promising it is discovered when a client asks for their
    // reimbursement paperwork.
    profile = completeProfile()
    clinician = { npi_number: null, taxonomy_code: null }
    const Body = STEP_BODIES.done
    render(<Body {...props(["self_pay"], false)} />)

    expect(screen.queryByText(/needs a superbill/i)).not.toBeInTheDocument()
  })

  it("promises one when there is an NPI", () => {
    profile = completeProfile()
    clinician = { npi_number: "1999999984", taxonomy_code: "101YM0800X" }
    const Body = STEP_BODIES.done
    render(<Body {...props(["self_pay"], false)} />)

    expect(screen.getByText(/needs a superbill/i)).toBeInTheDocument()
  })

  it("treats an unread profile as not ready rather than as ready", () => {
    // Both reads are in flight for a moment. Claiming readiness we have not
    // checked is the failure this whole section exists to avoid.
    profile = null
    const Body = STEP_BODIES.done
    render(<Body {...props(["own_insurance"], false)} />)

    expect(screen.queryByText("You're set up to bill")).not.toBeInTheDocument()
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

    expect(screen.getByText(/prepares and tracks applications/i)).toBeInTheDocument()
    expect(screen.queryByText(/put your applications in/i)).not.toBeInTheDocument()
  })

  it("separates being contracted from being able to file a claim", () => {
    const Body = STEP_BODIES.done
    render(<Body {...props(["platform"], true)} />)

    expect(screen.getByText(/billing a payer through pablo comes later/i)).toBeInTheDocument()
  })

  it("names what 'the service' is, rather than leaving it to be guessed", () => {
    // On its own, "the service" could be Pablo, a clearinghouse, or the
    // company that actually pays them. Only the last is meant, and this is the
    // first line on the screen.
    const Body = STEP_BODIES.done
    render(<Body {...props(["platform"], true)} />)

    expect(screen.getByText(/the service that bills for you today/i)).toBeInTheDocument()
  })

  it("does not claim the credentialing record is ready", () => {
    // Every field on the way here can be skipped, so arriving proves nothing
    // about whether the record is complete. Not on a platform and seeing
    // somebody, which is the branch that carries this heading.
    const Body = STEP_BODIES.done
    render(<Body {...props(["self_pay"], true)} />)

    expect(screen.getByText("Your credentialing record is saved")).toBeInTheDocument()
    expect(screen.queryByText(/credentialing record is ready/i)).not.toBeInTheDocument()
  })

  it("tells a platform clinician her current billing is untouched", () => {
    // The commonest fear about pointing a second system at your billing is
    // that it quietly starts moving money.
    const Body = STEP_BODIES.done
    render(<Body {...props(["platform"], true)} />)

    expect(screen.getByText(/before pablo changes where a payer sends/i)).toBeInTheDocument()
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
