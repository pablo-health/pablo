// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * MedicationsTab Component Tests
 *
 * Covers the populated list and empty-state rendering, and — the main
 * focus — that read-only deployment mode hides every write affordance
 * ("Add medication", per-row Edit/Discontinue/Delete) while the record
 * itself (drug name, dose, status badge) stays visible.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { MedicationsTab } from "../MedicationsTab"
import type { Medication, MedicationListResponse } from "@/types/medications"

let medsData: Medication[] = []
let medsLoading = false
let medsError: Error | null = null

const mutateAsync = vi.fn()

vi.mock("@/hooks/useMedications", () => ({
  usePatientMedications: () => ({
    data: { data: medsData, total: medsData.length } as MedicationListResponse,
    isLoading: medsLoading,
    error: medsError,
  }),
  useDeleteMedication: () => ({
    mutateAsync,
    isPending: false,
    variables: undefined,
  }),
  useUpdateMedication: () => ({
    mutateAsync,
    isPending: false,
    variables: undefined,
  }),
  useCreateMedication: () => ({
    mutateAsync,
    isPending: false,
    variables: undefined,
  }),
}))

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

function makeMedication(overrides: Partial<Medication> = {}): Medication {
  return {
    id: "med_1",
    patient_id: "patient_1",
    drug_name: "Sertraline",
    dose: "50 mg daily",
    status: "active",
    started_at: "2026-01-01",
    stopped_at: null,
    stop_reason: null,
    notes: null,
    created_by: "user_1",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  }
}

describe("MedicationsTab", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    medsData = []
    medsLoading = false
    medsError = null
  })

  describe("Rendering", () => {
    it("shows an empty state with an Add medication button when there are none", () => {
      render(<MedicationsTab patientId="patient_1" />)

      expect(screen.getByText("No medications recorded.")).toBeInTheDocument()
      expect(
        screen.getByRole("button", { name: "Add medication" })
      ).toBeInTheDocument()
    })

    it("lists medications with drug name, dose and status badge", () => {
      medsData = [makeMedication()]
      render(<MedicationsTab patientId="patient_1" />)

      expect(screen.getByText("Sertraline")).toBeInTheDocument()
      expect(screen.getByText("50 mg daily")).toBeInTheDocument()
      expect(screen.getByText("Active")).toBeInTheDocument()
    })

    it("shows Edit, Discontinue and Delete controls per row", () => {
      medsData = [makeMedication()]
      render(<MedicationsTab patientId="patient_1" />)

      expect(
        screen.getByRole("button", { name: "Edit Sertraline" })
      ).toBeInTheDocument()
      expect(
        screen.getByRole("button", { name: "Discontinue Sertraline" })
      ).toBeInTheDocument()
      expect(
        screen.getByRole("button", { name: "Delete Sertraline" })
      ).toBeInTheDocument()
    })
  })

  describe("read-only deployment mode", () => {
    afterEach(() => vi.unstubAllEnvs())

    it("hides Add medication in the populated list when read-only", () => {
      medsData = [makeMedication()]
      vi.stubEnv("NEXT_PUBLIC_READ_ONLY", "true")

      render(<MedicationsTab patientId="patient_1" />)

      expect(
        screen.queryByRole("button", { name: "Add medication" })
      ).not.toBeInTheDocument()
      // The record itself is still visible.
      expect(screen.getByText("Sertraline")).toBeInTheDocument()
      expect(screen.getByText("50 mg daily")).toBeInTheDocument()
      expect(screen.getByText("Active")).toBeInTheDocument()
    })

    it("hides per-row Edit, Discontinue and Delete controls when read-only", () => {
      medsData = [makeMedication()]
      vi.stubEnv("NEXT_PUBLIC_READ_ONLY", "true")

      render(<MedicationsTab patientId="patient_1" />)

      expect(
        screen.queryByRole("button", { name: "Edit Sertraline" })
      ).not.toBeInTheDocument()
      expect(
        screen.queryByRole("button", { name: "Discontinue Sertraline" })
      ).not.toBeInTheDocument()
      expect(
        screen.queryByRole("button", { name: "Delete Sertraline" })
      ).not.toBeInTheDocument()
    })

    it("hides Add medication in the empty state when read-only", () => {
      vi.stubEnv("NEXT_PUBLIC_READ_ONLY", "true")

      render(<MedicationsTab patientId="patient_1" />)

      expect(screen.getByText("No medications recorded.")).toBeInTheDocument()
      expect(
        screen.queryByRole("button", { name: "Add medication" })
      ).not.toBeInTheDocument()
    })

    it("shows all controls when the deployment flag is unset", () => {
      medsData = [makeMedication()]

      render(<MedicationsTab patientId="patient_1" />)

      expect(
        screen.getByRole("button", { name: "Add medication" })
      ).toBeInTheDocument()
      expect(
        screen.getByRole("button", { name: "Edit Sertraline" })
      ).toBeInTheDocument()
      expect(
        screen.getByRole("button", { name: "Discontinue Sertraline" })
      ).toBeInTheDocument()
      expect(
        screen.getByRole("button", { name: "Delete Sertraline" })
      ).toBeInTheDocument()
    })
  })
})
