// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Billing setup: the question every therapist answers, and the promise that
 * leaving mid-way costs her nothing.
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
// for the billing profile and appointment types, and — now that it leads the
// credentialing routes — the NPI lookup. These tests are about routing and
// resuming, so those stand in as markers; what they render is their own tests'
// business.
vi.mock("@/components/credentialing/NpiLookupStep", () => ({
  NpiLookupStep: () => <div>npi lookup step</div>,
}))

vi.mock("../SetupSteps", () => ({
  PracticeIdentityStep: () => <div>practice identity step</div>,
  BillingContactStep: () => <div>billing contact step</div>,
  RatesStep: () => <div>rates step</div>,
  PrivatePayDoneStep: () => <div>private pay done</div>,
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

beforeEach(() => {
  vi.clearAllMocks()
  usePreferences.mockReturnValue({ data: prefs() })
})

describe("the first screen", () => {
  it("asks how she is paid today, not what she wants", () => {
    // Situational, not aspirational: the routing depends on what is true, and
    // a question about wishes invites an answer about the next six months.
    render(<GetPaidWizard />)

    expect(screen.getByText("How do you get paid today?")).toBeInTheDocument()
    expect(screen.queryByText(/wish|would you like/i)).not.toBeInTheDocument()
  })

  it("offers the four situations", () => {
    render(<GetPaidWizard />)

    expect(screen.getAllByRole("button", { pressed: false })).toHaveLength(4)
    expect(screen.getByText("My clients pay me directly")).toBeInTheDocument()
  })

  it("moves her on as soon as she picks one", () => {
    render(<GetPaidWizard />)

    fireEvent.click(screen.getByText("My clients pay me directly"))

    expect(screen.queryByText("How do you get paid today?")).not.toBeInTheDocument()
  })
})

describe("remembering where she stopped", () => {
  it("saves the answer and the step she lands on", () => {
    render(<GetPaidWizard />)

    fireEvent.click(screen.getByText("My clients pay me directly"))

    // The NPI lookup leads every route now, so the step she lands on after
    // answering is the lookup rather than the first practice form.
    expect(savePreferences).toHaveBeenCalledWith(
      expect.objectContaining({
        billing_setup_route: "private_pay",
        billing_setup_step: "confirm",
      }),
    )
  })

  it("opens where she left off, on the branch she chose", () => {
    // Closing the tab mid-setup should cost her nothing.
    usePreferences.mockReturnValue({
      data: prefs({ billing_setup_route: "wants_panels", billing_setup_step: "payers" }),
    })

    render(<GetPaidWizard />)

    expect(screen.queryByText("How do you get paid today?")).not.toBeInTheDocument()
    // The step's own heading, not just its entry in the stepper — she is ON
    // the payers step, not merely able to reach it.
    expect(screen.getByRole("heading", { name: "Payers" })).toBeInTheDocument()
  })

  it("falls back to the start of her branch when the step no longer exists", () => {
    // A step renamed or removed since she was last here must not strand her on
    // a blank screen.
    usePreferences.mockReturnValue({
      data: prefs({ billing_setup_route: "private_pay", billing_setup_step: "a-step-we-deleted" }),
    })

    render(<GetPaidWizard />)

    expect(screen.getByText("How do you get paid today?")).toBeInTheDocument()
  })

  it("records each step as she moves through", () => {
    render(<GetPaidWizard />)

    fireEvent.click(screen.getByText("My clients pay me directly"))
    savePreferences.mockClear()
    fireEvent.click(screen.getByRole("button", { name: "Back" }))

    expect(savePreferences).toHaveBeenCalledWith(
      expect.objectContaining({ billing_setup_step: "route" }),
    )
  })

  it("treats finishing later as settled, so she is never trapped here", () => {
    // The same call the calendar wizard makes. She gets a card on the billing
    // page instead, and can come back whenever.
    const onSettled = vi.fn()
    render(<GetPaidWizard onSettled={onSettled} />)

    fireEvent.click(screen.getByRole("button", { name: "Finish later" }))

    expect(savePreferences).toHaveBeenCalledWith(
      expect.objectContaining({ billing_setup_complete: true }),
    )
    expect(onSettled).toHaveBeenCalled()
  })
})

describe("the steps she is shown", () => {
  it("shows only the shared spine before she answers", () => {
    // A stepper that grew three entries the moment she clicked would make the
    // choice feel like it cost her something.
    render(<GetPaidWizard />)

    expect(screen.getByText("Practice identity")).toBeInTheDocument()
    expect(screen.getByText("Billing contact")).toBeInTheDocument()
    expect(screen.queryByText("Payers")).not.toBeInTheDocument()
  })

  it("adds no payer or record steps for a private-pay practice", () => {
    render(<GetPaidWizard />)

    fireEvent.click(screen.getByText("My clients pay me directly"))

    expect(screen.queryByText("Payers")).not.toBeInTheDocument()
    expect(screen.queryByText("Your record")).not.toBeInTheDocument()
  })

  it("still looks up the NPI for a private-pay practice", () => {
    // She bills nobody, and still needs it: superbill.py requires the
    // rendering provider's NPI, so without one we cannot produce the document
    // her client files for reimbursement.
    render(<GetPaidWizard />)

    fireEvent.click(screen.getByText("My clients pay me directly"))

    expect(screen.getByText("What we found")).toBeInTheDocument()
  })

  it("adds the payer step, and no record steps, for someone already paneled", () => {
    render(<GetPaidWizard />)

    fireEvent.click(screen.getByText("I’m already on insurance panels"))

    expect(screen.getByText("Payers")).toBeInTheDocument()
    // She is paneled. Nothing should walk her through credentialing she has
    // already done.
    expect(screen.queryByText("Your record")).not.toBeInTheDocument()
  })

  it("looks up the NPI for someone already paneled", () => {
    // She files claims herself, and her individual NPI is the rendering
    // provider on every one. This route used to have no NPI step at all.
    render(<GetPaidWizard />)

    fireEvent.click(screen.getByText("I’m already on insurance panels"))

    expect(screen.getByText("What we found")).toBeInTheDocument()
  })

  it("adds the record steps for someone who wants a panel", () => {
    render(<GetPaidWizard />)

    fireEvent.click(screen.getByText("I want to accept insurance, but I’m not on a panel yet"))

    expect(screen.getByText("Payers")).toBeInTheDocument()
    expect(screen.getByText("Your record")).toBeInTheDocument()
  })
})
