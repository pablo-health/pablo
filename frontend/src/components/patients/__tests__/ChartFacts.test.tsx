// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ChartFacts Component Tests
 *
 * The chart header's diagnoses and allergies: each reads "none recorded"
 * rather than vanishing, NKDA is its own answer, and the allergy dialog sends
 * the whole record in one of its three states.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { ChartFacts, allergiesSummary } from "../ChartFacts"
import { createMockPatient } from "@/test/factories"

const updateAllergies = vi.fn()

vi.mock("@/hooks/usePatients", () => ({
  useUpdateAllergies: () => ({ mutateAsync: updateAllergies, isPending: false }),
}))

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

describe("ChartFacts", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    updateAllergies.mockResolvedValue(createMockPatient())
  })

  it("reads none recorded and not recorded on an empty chart", () => {
    render(<ChartFacts patient={createMockPatient({ diagnosis: null })} />)
    expect(screen.getByTestId("chart-diagnoses")).toHaveTextContent("Diagnoses:None recorded")
    expect(screen.getByTestId("chart-allergies")).toHaveTextContent("Allergies:Not recorded")
  })

  it("shows the derived diagnosis line and each allergy state", () => {
    render(
      <ChartFacts
        patient={createMockPatient({ diagnosis: "Generalized anxiety disorder (F41.1)" })}
      />,
    )
    expect(screen.getByTestId("chart-diagnoses")).toHaveTextContent(
      "Generalized anxiety disorder (F41.1)",
    )
    expect(allergiesSummary(createMockPatient({ allergy_status: "nkda" }))).toBe(
      "No known drug allergies",
    )
    expect(
      allergiesSummary(
        createMockPatient({
          allergy_status: "recorded",
          allergies: [{ substance: "Penicillin", reaction: "Hives" }, { substance: "Sulfa" }],
        }),
      ),
    ).toBe("Penicillin (Hives), Sulfa")
  })

  it("records no known drug allergies as its own state", async () => {
    render(<ChartFacts patient={createMockPatient({ id: "patient_1" })} />)

    fireEvent.click(screen.getByRole("button", { name: "Edit allergies" }))
    fireEvent.click(screen.getByLabelText("No known drug allergies"))
    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() =>
      expect(updateAllergies).toHaveBeenCalledWith({
        patientId: "patient_1",
        data: { status: "nkda", allergies: [] },
      }),
    )
  })

  it("needs at least one allergy before a list can be saved", async () => {
    render(<ChartFacts patient={createMockPatient({ id: "patient_1" })} />)

    fireEvent.click(screen.getByRole("button", { name: "Edit allergies" }))
    fireEvent.click(screen.getByRole("radio", { name: "Allergies" }))
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()

    fireEvent.change(screen.getByLabelText("Allergy 1"), { target: { value: "Penicillin" } })
    fireEvent.change(screen.getByLabelText("Reaction 1"), { target: { value: "Hives" } })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() =>
      expect(updateAllergies).toHaveBeenCalledWith({
        patientId: "patient_1",
        data: {
          status: "recorded",
          allergies: [{ substance: "Penicillin", reaction: "Hives" }],
        },
      }),
    )
  })
})
