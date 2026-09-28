// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * CoverageDialog tests — the client's sex on the insurance card.
 *
 * It is asked here, not on the client form, and stored on the client: the
 * dialog pre-fills it from the client, saves it through the patient
 * endpoint only when it changed, and still asks for the subscriber's when
 * the subscriber is somebody else. The coverage and patient hooks are
 * mocked so the saves can be observed directly.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { CoverageDialog } from "../CoverageDialog"
import type { PayerResponse } from "@/types/coverage"

const mockCreate = vi.fn()
const mockUpdatePatient = vi.fn()
const mockUsePatient = vi.fn()

const PAYER: PayerResponse = {
  id: "payer-1",
  name: "Aetna",
  payer_id: "60054",
  clearinghouse_payer_id: null,
  is_carveout: false,
  carveout_of: null,
  enrollment_status: "none",
  enroll_eligibility: true,
  enroll_claims: true,
  enroll_remittance: false,
  timely_filing_days: 90,
  corrected_claim_days: 90,
  appeal_days: 180,
  created_at: "2026-09-01T10:00:00Z",
  updated_at: "2026-09-01T10:00:00Z",
}

vi.mock("@/hooks/useCoverage", () => ({
  usePayers: () => ({ data: { data: [PAYER] } }),
  useCreateCoverage: () => ({ mutateAsync: mockCreate, isError: false }),
  useUpdateCoverage: () => ({ mutateAsync: vi.fn(), isError: false }),
}))

vi.mock("@/hooks/usePatients", () => ({
  usePatient: (...args: unknown[]) => mockUsePatient(...args),
  useUpdatePatient: () => ({ mutateAsync: mockUpdatePatient, isError: false }),
}))

function renderDialog() {
  return render(
    <CoverageDialog patientId="patient-1" coverage={null} open={true} onOpenChange={vi.fn()} />
  )
}

async function fillPlan(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("combobox", { name: "Insurance company" }))
  await user.click(screen.getByRole("option", { name: /Aetna/ }))
  await user.type(screen.getByLabelText("Member ID"), "W123456789")
}

describe("CoverageDialog — sex on insurance card", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUsePatient.mockReturnValue({ data: { id: "patient-1", sex: null } })
    mockCreate.mockResolvedValue({})
    mockUpdatePatient.mockResolvedValue({})
  })

  it("saves the client's sex on the client when the client is the subscriber", async () => {
    const user = userEvent.setup()
    renderDialog()

    await fillPlan(user)
    await user.click(screen.getByRole("combobox", { name: "Sex on insurance card" }))
    await user.click(screen.getByRole("option", { name: "X or unspecified" }))
    await user.click(screen.getByRole("button", { name: "Add coverage" }))

    await waitFor(() => {
      expect(mockUpdatePatient).toHaveBeenCalledWith({
        patientId: "patient-1",
        data: { sex: "U" },
      })
    })
    expect(mockCreate.mock.calls[0][0].data.subscriber_sex).toBeNull()
  })

  it("pre-fills from the client and does not re-save an unchanged value", async () => {
    mockUsePatient.mockReturnValue({ data: { id: "patient-1", sex: "F" } })
    const user = userEvent.setup()
    renderDialog()

    expect(screen.getByRole("combobox", { name: "Sex on insurance card" })).toHaveTextContent(
      "Female"
    )
    await fillPlan(user)
    await user.click(screen.getByRole("button", { name: "Add coverage" }))

    await waitFor(() => expect(mockCreate).toHaveBeenCalled())
    expect(mockUpdatePatient).not.toHaveBeenCalled()
  })

  it("asks for the client's and the subscriber's separately when they differ", async () => {
    const user = userEvent.setup()
    renderDialog()

    await fillPlan(user)
    await user.click(screen.getByRole("combobox", { name: "Relationship to subscriber" }))
    await user.click(screen.getByRole("option", { name: "Child" }))

    await user.click(screen.getByRole("combobox", { name: "Client's sex" }))
    await user.click(screen.getByRole("option", { name: "Male" }))
    await user.click(screen.getByRole("combobox", { name: "Sex on insurance card" }))
    await user.click(screen.getByRole("option", { name: "Female" }))
    await user.click(screen.getByRole("button", { name: "Add coverage" }))

    await waitFor(() => {
      expect(mockUpdatePatient).toHaveBeenCalledWith({
        patientId: "patient-1",
        data: { sex: "M" },
      })
    })
    expect(mockCreate.mock.calls[0][0].data.subscriber_sex).toBe("F")
  })
})
