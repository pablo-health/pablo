// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Billing setup: the checklist every therapist answers, and the promise that
 * leaving mid-way costs her nothing.
 *
 * The bug classes here are about her ANSWER being lost or misread:
 *
 * - A tick that is accepted on screen and silently not saved, so she answers,
 *   moves on, and meets the checklist again next time.
 * - "Not seeing clients yet" reading back as "has not answered". It is a real
 *   answer, and the only reason the stored shape is a list that can be empty
 *   rather than a nullable route.
 * - A clinician who answered the superseded single-select question being sent
 *   back to an empty checklist, having already told us.
 */

import { fireEvent, render, screen } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { UserPreferences } from "@/lib/api/users"
import { GetPaidWizard } from "../GetPaidWizard"

const usePreferences = vi.hoisted(() => vi.fn())
const savePreferences = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/usePreferences", () => ({
  usePreferences: (...args: unknown[]) => usePreferences(...args),
  useSavePreferences: () => ({ mutate: savePreferences, isPending: false }),
}))

// Several steps mount components that fetch their own data: the settings cards
// for the billing profile and appointment types, and — now that it leads every
// route — the NPI lookup. These tests are about answering and resuming, so
// those stand in as markers; what they render is their own tests' business.
vi.mock("@/components/credentialing/NpiLookupStep", () => ({
  NpiLookupStep: () => <div>npi lookup step</div>,
}))

vi.mock("../SetupSteps", () => ({
  PracticeIdentityStep: () => <div>practice identity step</div>,
  BillingContactStep: () => <div>billing contact step</div>,
  RatesStep: () => <div>rates step</div>,
  PayersStep: () => <h2>Payers</h2>,
  CredentialingRecordStep: () => <div>credentialing record step</div>,
}))

function prefs(overrides: Partial<UserPreferences> = {}): UserPreferences {
  return {
    default_session_type: "individual",
    default_duration_minutes: 50,
    auto_transcribe: true,
    quality_preset: "balanced",
    therapist_display_name: null,
    calendar_default_view: "timeGridWeek",
    timezone: "America/New_York",
    theme: "warm-paper",
    calendar_density: "balanced",
    ...overrides,
  } as UserPreferences
}

const PLATFORM = /a service like Headway, Alma, or Rula/
const SELF_PAY = /Clients pay me directly/

beforeEach(() => {
  vi.clearAllMocks()
  usePreferences.mockReturnValue({ data: prefs() })
})

describe("the checklist", () => {
  it("asks how she is paid today, not what she wants", () => {
    // Situational, not aspirational: a question about wishes invites an answer
    // about the next six months and routes her on it.
    render(<GetPaidWizard />)

    expect(screen.getByText("How do clients pay you today?")).toBeInTheDocument()
    expect(screen.queryByText(/wish|would you like/i)).not.toBeInTheDocument()
  })

  it("lets several answers be true at once", () => {
    // The whole reason for the redesign: a therapist on Headway who also sees
    // clients privately could not describe herself with a single choice.
    render(<GetPaidWizard />)

    fireEvent.click(screen.getByLabelText(PLATFORM))
    fireEvent.click(screen.getByLabelText(SELF_PAY))

    expect(screen.getByLabelText(PLATFORM)).toBeChecked()
    expect(screen.getByLabelText(SELF_PAY)).toBeChecked()
  })

  it("names the platforms, because nobody says 'I'm on a platform'", () => {
    render(<GetPaidWizard />)

    expect(screen.getByLabelText(PLATFORM)).toBeInTheDocument()
  })

  it("will not continue on no answer at all", () => {
    render(<GetPaidWizard />)

    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled()
    expect(screen.getByText(/Choose at least one option/)).toBeInTheDocument()
  })

  it("saves nothing until she continues", () => {
    // Ticking is not answering. This is the screen she is most likely to
    // change her mind on mid-thought, and a box tried and untried again should
    // leave no trace.
    render(<GetPaidWizard />)

    fireEvent.click(screen.getByLabelText(PLATFORM))

    expect(savePreferences).not.toHaveBeenCalled()
  })

  it("saves what she ticked when she continues", () => {
    render(<GetPaidWizard />)

    fireEvent.click(screen.getByLabelText(PLATFORM))
    fireEvent.click(screen.getByLabelText(SELF_PAY))
    fireEvent.click(screen.getByRole("button", { name: "Continue" }))

    expect(savePreferences).toHaveBeenCalledWith(
      expect.objectContaining({
        billing_setup_state: ["platform", "self_pay"],
        billing_setup_step: "plan",
      }),
    )
  })
})

describe("not seeing clients yet", () => {
  it("is saved as an answer, not as silence", () => {
    // The empty list IS the answer. If this saved null she would be asked
    // again next time, having already told us.
    render(<GetPaidWizard />)

    fireEvent.click(screen.getByRole("button", { name: /not seeing clients yet/i }))

    expect(savePreferences).toHaveBeenCalledWith(
      expect.objectContaining({ billing_setup_state: [], billing_setup_step: "plan" }),
    )
  })

  it("moves her on rather than leaving her on the question", () => {
    render(<GetPaidWizard />)

    fireEvent.click(screen.getByRole("button", { name: /not seeing clients yet/i }))

    expect(screen.getByText("What Pablo will help you set up")).toBeInTheDocument()
  })
})

describe("the plan screen", () => {
  it("is where the credentialing ask lives, unticked", () => {
    usePreferences.mockReturnValue({
      data: prefs({ billing_setup_state: ["platform"], billing_setup_step: "plan" }),
    })
    render(<GetPaidWizard />)

    expect(screen.getByTestId("wants-credentialing")).not.toBeChecked()
  })

  it("saves the ask as its own fact", () => {
    usePreferences.mockReturnValue({
      data: prefs({ billing_setup_state: ["platform"], billing_setup_step: "plan" }),
    })
    render(<GetPaidWizard />)

    fireEvent.click(screen.getByTestId("wants-credentialing"))

    expect(savePreferences).toHaveBeenCalledWith(
      expect.objectContaining({ billing_setup_wants_credentialing: true }),
    )
  })

  it("adds the screens an application needs, and only then", () => {
    // Ticking it ADDS steps. It must not enable anything by itself.
    usePreferences.mockReturnValue({
      data: prefs({
        billing_setup_state: ["platform"],
        billing_setup_wants_credentialing: true,
        billing_setup_step: "record",
      }),
    })
    render(<GetPaidWizard />)

    expect(screen.getByText("credentialing record step")).toBeInTheDocument()
  })
})

describe("the platform clinician taking cash, end to end", () => {
  it("walks the spine and is never asked about payers", () => {
    // The case the redesign exists for. Her platform handles the insurance
    // side, so being on one adds nothing — the tick buys restraint.
    usePreferences.mockReturnValue({
      data: prefs({ billing_setup_state: ["self_pay", "platform"] }),
    })
    render(<GetPaidWizard />)

    expect(screen.queryByRole("heading", { name: "Payers" })).not.toBeInTheDocument()
  })

  it("resumes on the step she left, not at the checklist", () => {
    usePreferences.mockReturnValue({
      data: prefs({
        billing_setup_state: ["self_pay", "platform"],
        billing_setup_step: "rates",
      }),
    })
    render(<GetPaidWizard />)

    expect(screen.getByText("rates step")).toBeInTheDocument()
  })
})

describe("a clinician who answered the superseded question", () => {
  it("resumes from it rather than meeting an empty checklist", () => {
    // Her old answer is read forward by the model. She has already told us
    // she is on a platform and wants her own contracts; asking again would be
    // the product forgetting.
    usePreferences.mockReturnValue({
      data: prefs({
        billing_setup_state: ["platform"],
        billing_setup_wants_credentialing: true,
        billing_setup_step: "record",
      }),
    })
    render(<GetPaidWizard />)

    expect(screen.getByText("credentialing record step")).toBeInTheDocument()
  })
})

describe("leaving mid-way", () => {
  it("marks setup settled so the page stops opening on it", () => {
    // A first-visit surface she cannot leave is a trap, not a wizard.
    const onSettled = vi.fn()
    render(<GetPaidWizard onSettled={onSettled} />)

    fireEvent.click(screen.getByRole("button", { name: /finish later/i }))

    expect(savePreferences).toHaveBeenCalledWith(
      expect.objectContaining({ billing_setup_complete: true }),
    )
    expect(onSettled).toHaveBeenCalled()
  })

  it("stays inert until her saved answers are in hand", () => {
    // `remember` cannot write without them, so a click landing first would be
    // accepted on screen and silently not saved.
    usePreferences.mockReturnValue({ data: undefined })
    render(<GetPaidWizard />)

    expect(screen.getByTestId("wizard-loading")).toBeInTheDocument()
  })
})
