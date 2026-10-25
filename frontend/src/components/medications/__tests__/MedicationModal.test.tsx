// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * MedicationModal: whether a medication is harmful to interrupt.
 *
 * The default list lives on the server, so the modal shows what it says
 * ("Default (Yes)") and sends the clinician's own call, or null to go back
 * to the default. The failures these catch: the clinician's call not sent,
 * the default folded into an explicit yes, or the default's answer hidden.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MedicationModal } from "../MedicationModal"
import type { Medication } from "@/types/medications"

const createMedication = vi.fn()
const updateMedication = vi.fn()

vi.mock("@/hooks/useMedications", () => ({
  useCreateMedication: () => ({ mutateAsync: createMedication, isPending: false }),
  useUpdateMedication: () => ({ mutateAsync: updateMedication, isPending: false }),
}))

function lithium(overrides: Partial<Medication> = {}): Medication {
  return {
    id: "med_1",
    patient_id: "patient_1",
    drug_name: "Lithium carbonate",
    dose: "300 mg",
    frequency: "twice daily",
    category: "psychiatric",
    status: "active",
    started_at: "2026-01-01",
    stopped_at: null,
    stop_reason: null,
    notes: null,
    interruption_risk: null,
    interruption_risk_default: true,
    created_by: "user_1",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  }
}

function renderEdit(medication: Medication) {
  render(
    <MedicationModal
      patientId="patient_1"
      open
      onOpenChange={() => {}}
      initialData={medication}
    />,
  )
}

describe("MedicationModal, harmful to interrupt", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("shows what the default list says for the medication", () => {
    renderEdit(lithium())

    expect(screen.getByRole("combobox", { name: "Harmful to interrupt" })).toHaveTextContent(
      "Default (Yes)",
    )
  })

  it("sends the clinician's no, overriding a listed medication", async () => {
    const user = userEvent.setup()
    renderEdit(lithium())

    await user.click(screen.getByRole("combobox", { name: "Harmful to interrupt" }))
    await user.click(screen.getByRole("option", { name: "No" }))
    await user.click(screen.getByRole("button", { name: "Save changes" }))

    expect(updateMedication).toHaveBeenCalledWith(
      expect.objectContaining({ data: expect.objectContaining({ interruption_risk: false }) }),
    )
  })

  it("sends null to go back to the default, not an explicit yes", async () => {
    const user = userEvent.setup()
    renderEdit(lithium({ interruption_risk: false }))
    const picker = screen.getByRole("combobox", { name: "Harmful to interrupt" })
    expect(picker).toHaveTextContent("No")

    await user.click(picker)
    await user.click(screen.getByRole("option", { name: "Default (Yes)" }))
    await user.click(screen.getByRole("button", { name: "Save changes" }))

    expect(updateMedication).toHaveBeenCalledWith(
      expect.objectContaining({ data: expect.objectContaining({ interruption_risk: null }) }),
    )
  })

  it("lets a new medication be marked before it is saved", async () => {
    const user = userEvent.setup()
    render(<MedicationModal patientId="patient_1" open onOpenChange={() => {}} />)

    await user.type(screen.getByLabelText("Drug name *"), "Clozapine")
    await user.type(screen.getByLabelText("Dose *"), "100 mg")
    const picker = screen.getByRole("combobox", { name: "Harmful to interrupt" })
    expect(picker).toHaveTextContent("Default")
    await user.click(picker)
    await user.click(screen.getByRole("option", { name: "Yes" }))
    await user.click(screen.getByRole("button", { name: "Add medication" }))

    expect(createMedication).toHaveBeenCalledWith(
      expect.objectContaining({ data: expect.objectContaining({ interruption_risk: true }) }),
    )
  })
})
