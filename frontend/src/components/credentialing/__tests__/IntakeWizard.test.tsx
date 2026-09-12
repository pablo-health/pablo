// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The wizard's load-bearing behaviours, which are design decisions rather than
 * styling: tier navigation, the supervision fork changing the question set,
 * and the stop-after-Tier-1 path reading as a finish rather than an
 * abandonment.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type {
  Confirmation,
  IntakeField,
  IntakeSurface,
  IntakeTier,
} from "@/types/credentialing"
import { IntakeWizard } from "../IntakeWizard"

const useIntake = vi.hoisted(() => vi.fn())
const useConfirmations = vi.hoisted(() => vi.fn())
const recordMutate = vi.hoisted(() => vi.fn())
const saveMutate = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useCredentialingIntake", () => ({
  useIntake: (...args: unknown[]) => useIntake(...args),
  useConfirmations: (...args: unknown[]) => useConfirmations(...args),
  useRecordConfirmation: () => ({ mutate: recordMutate, isPending: false }),
  useSaveIntakeAnswers: () => ({ mutate: saveMutate, isPending: false }),
}))

function field(overrides: Partial<IntakeField> = {}): IntakeField {
  return {
    key: "npi_number",
    label: "Individual NPI",
    section: "professional_ids",
    tier: "tier_0_confirm",
    kind: "text",
    required: true,
    applies_to: "all",
    source: "nppes",
    help_text: null,
    choices: [],
    answered: false,
    current_value: null,
    ...overrides,
  }
}

function surface(overrides: Partial<IntakeSurface> = {}): IntakeSurface {
  return {
    supervised: false,
    prescriber: false,
    claims_ready: false,
    progress: [
      { tier: "tier_0_confirm", answered: 0, required: 3, complete: false },
      { tier: "tier_1_claims_ready", answered: 0, required: 8, complete: false },
      { tier: "tier_2_credentialing", answered: 0, required: 14, complete: false },
    ],
    fields: [field()],
    ...overrides,
  }
}

function renderWizard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <IntakeWizard />
    </QueryClientProvider>,
  )
}

function goToTier(label: string) {
  fireEvent.click(screen.getByRole("tab", { name: new RegExp(label, "i") }))
}

beforeEach(() => {
  vi.clearAllMocks()
  useIntake.mockReturnValue({ data: surface(), isLoading: false })
  useConfirmations.mockReturnValue({ data: [] as Confirmation[] })
})

describe("tier navigation", () => {
  it("offers all three tiers, each with its own count", () => {
    renderWizard()

    const tabs = screen.getAllByRole("tab")
    expect(tabs).toHaveLength(3)
    expect(screen.getByText("0/8")).toBeInTheDocument()
    expect(screen.getByText("0/14")).toBeInTheDocument()
  })

  it("never shows one overall figure", () => {
    // A single percentage would render finishing Tier 1 and stopping as
    // roughly half done, which is the nag the design rules out.
    renderWizard()

    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument()
    expect(screen.queryByText(/% complete/i)).not.toBeInTheDocument()
  })

  it("starts on the tier that asks nothing", () => {
    renderWizard()

    expect(screen.getByText(/there is nothing to type/i)).toBeInTheDocument()
  })
})

describe("tier 0 confirmations", () => {
  it("shows the value with where it came from, not an empty box", () => {
    useConfirmations.mockReturnValue({
      data: [
        {
          field_key: "npi_number",
          source: "nppes",
          presented_value: "1999999984",
          confirmed: false,
          correction: "1234567893",
          confirmed_at: "2026-09-01T00:00:00Z",
        },
      ],
    })
    renderWizard()

    expect(screen.getByText("1999999984")).toBeInTheDocument()
    expect(screen.getByText(/from the NPPES registry/i)).toBeInTheDocument()
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument()
  })

  it("records a confirmation rather than only marking the screen", () => {
    renderWizard()

    fireEvent.click(screen.getByRole("button", { name: /looks right/i }))

    expect(recordMutate).toHaveBeenCalledWith({
      fieldKey: "npi_number",
      payload: expect.objectContaining({ source: "nppes", confirmed: true }),
    })
  })

  it("will not send a rejection without saying what is right", () => {
    renderWizard()

    fireEvent.click(screen.getByRole("button", { name: /not right/i }))
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }))

    expect(recordMutate).not.toHaveBeenCalled()
  })

  it("sends the correction once she gives one", () => {
    renderWizard()

    fireEvent.click(screen.getByRole("button", { name: /not right/i }))
    fireEvent.change(screen.getByLabelText(/what should it say/i), {
      target: { value: "1234567893" },
    })
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }))

    expect(recordMutate).toHaveBeenCalledWith({
      fieldKey: "npi_number",
      payload: expect.objectContaining({
        confirmed: false,
        correction: "1234567893",
      }),
    })
  })
})

describe("the supervision fork", () => {
  const tierOneFields: IntakeField[] = [
    field({
      key: "licenses",
      label: "Every licence you hold",
      tier: "tier_1_claims_ready",
      kind: "collection",
      source: null,
    }),
  ]

  it("asks the fork before anything it changes", () => {
    useIntake.mockReturnValue({
      data: surface({ fields: tierOneFields }),
      isLoading: false,
    })
    renderWizard()
    goToTier("About your practice")

    expect(
      screen.getByText(/independently licensed, or practising under supervision/i),
    ).toBeInTheDocument()
  })

  it("re-asks the server for the other branch's questions, and saves the answer", () => {
    useIntake.mockReturnValue({
      data: surface({ fields: tierOneFields }),
      isLoading: false,
    })
    renderWizard()
    goToTier("About your practice")

    fireEvent.click(screen.getByRole("button", { name: /under supervision/i }))

    // The branch is asked of the API, not decided in the client — the question
    // set is the server's and the client keeps no copy of the rule.
    expect(useIntake).toHaveBeenLastCalledWith({ supervised: true })
    expect(saveMutate).toHaveBeenCalledWith({ supervision_status: "supervised" })
  })
})

describe("stopping after tier 1", () => {
  const readyFields: IntakeField[] = [
    field({
      key: "licenses",
      label: "Every licence you hold",
      tier: "tier_1_claims_ready",
      kind: "collection",
      source: null,
      answered: true,
    }),
  ]

  function readySurface(): IntakeSurface {
    return surface({
      claims_ready: true,
      fields: readyFields,
      progress: [
        { tier: "tier_0_confirm", answered: 3, required: 3, complete: true },
        { tier: "tier_1_claims_ready", answered: 8, required: 8, complete: true },
        { tier: "tier_2_credentialing", answered: 0, required: 14, complete: false },
      ],
    })
  }

  it("says she is done rather than pointing at what is left", () => {
    useIntake.mockReturnValue({ data: readySurface(), isLoading: false })
    renderWizard()
    goToTier("About your practice")

    expect(screen.getByText(/you are done/i)).toBeInTheDocument()
  })

  it("does not nag toward the panels tier", () => {
    useIntake.mockReturnValue({ data: readySurface(), isLoading: false })
    renderWizard()
    goToTier("About your practice")

    expect(screen.queryByText(/finish/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/incomplete/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/remaining/i)).not.toBeInTheDocument()
  })

  it("frames the finish for a clinician who may never bill through us", () => {
    // Someone who bought Pablo for credentialing alone must not be told her
    // reward is that we can bill for her.
    useIntake.mockReturnValue({ data: readySurface(), isLoading: false })
    renderWizard()
    goToTier("About your practice")

    expect(screen.getByText(/a payer application asks for/i)).toBeInTheDocument()
  })

  it("marks the panels tier optional without marking it failed", () => {
    useIntake.mockReturnValue({ data: readySurface(), isLoading: false })
    renderWizard()
    goToTier("Applying to panels")

    expect(screen.getByText(/leave this for another day/i)).toBeInTheDocument()
  })
})

describe("questions that are not confirmations", () => {
  it("says which are optional and which are already on file", () => {
    useIntake.mockReturnValue({
      data: surface({
        fields: [
          field({
            key: "bank_account",
            label: "Where payments should land",
            tier: "tier_1_claims_ready" as IntakeTier,
            kind: "collection",
            required: false,
            source: null,
            answered: false,
          }),
          field({
            key: "licenses",
            label: "Every licence you hold",
            tier: "tier_1_claims_ready" as IntakeTier,
            kind: "collection",
            source: null,
            answered: true,
          }),
        ],
      }),
      isLoading: false,
    })
    renderWizard()
    goToTier("About your practice")

    expect(screen.getByText("Optional")).toBeInTheDocument()
    expect(screen.getByText("On file")).toBeInTheDocument()
    expect(screen.getByText("Not yet")).toBeInTheDocument()
  })
})

describe("tier 0 shows what the record holds", () => {
  it("shows a value that is on file before she has confirmed anything", () => {
    // The bug: the API carried no value, so the card asked her to agree with
    // "Nothing on file" for an NPI sitting on her own profile.
    useIntake.mockReturnValue({
      data: surface({ fields: [field({ current_value: "1999999984" })] }),
      isLoading: false,
    })
    useConfirmations.mockReturnValue({ data: [] as Confirmation[] })
    renderWizard()

    expect(screen.getByText("1999999984")).toBeInTheDocument()
    expect(screen.queryByText(/nothing on file/i)).not.toBeInTheDocument()
  })

  it("still says so when there is genuinely nothing behind a field", () => {
    renderWizard()

    expect(screen.getByText(/nothing on file/i)).toBeInTheDocument()
  })

  it("prefers the record over the value she agreed with last time", () => {
    // A column that has changed since she confirmed it is the one thing this
    // tier exists to surface. Showing the old value would hide it.
    useIntake.mockReturnValue({
      data: surface({ fields: [field({ current_value: "1999999984", answered: true })] }),
      isLoading: false,
    })
    useConfirmations.mockReturnValue({
      data: [
        {
          field_key: "npi_number",
          source: "nppes",
          presented_value: "1000000004",
          confirmed: true,
          correction: null,
          confirmed_at: "2026-09-01T00:00:00Z",
        },
      ],
    })
    renderWizard()

    expect(screen.getByText("1999999984")).toBeInTheDocument()
    expect(screen.queryByText("1000000004")).not.toBeInTheDocument()
  })

  it("confirms the value she was actually shown", () => {
    // So a later divergence between card and column is visible in the record,
    // rather than the confirmation quietly agreeing with something else.
    useIntake.mockReturnValue({
      data: surface({ fields: [field({ current_value: "1999999984" })] }),
      isLoading: false,
    })
    useConfirmations.mockReturnValue({ data: [] as Confirmation[] })
    renderWizard()

    fireEvent.click(screen.getByRole("button", { name: /looks right/i }))

    expect(recordMutate).toHaveBeenCalledWith({
      fieldKey: "npi_number",
      payload: expect.objectContaining({ presented_value: "1999999984" }),
    })
  })
})
