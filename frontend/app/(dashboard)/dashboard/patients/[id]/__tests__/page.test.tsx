// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Tests for the patient chart page.
 *
 * Note: mirrors the sessions detail page tests — logic is exercised through
 * a small wrapper that takes `patientId` as a prop instead of the async
 * `params` promise, which needs a full Next.js environment to resolve.
 */

import { readFileSync } from "fs"
import { join } from "path"
import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen } from "@testing-library/react"
import * as usePatients from "@/hooks/usePatients"
import * as useCoverage from "@/hooks/useCoverage"
import * as usePayments from "@/hooks/usePayments"
import { renderWithProviders } from "@/test/renderWithProviders"
import { createMockPatient } from "@/test/factories"
import { PatientSummary } from "@/components/patients/PatientSummary"

vi.mock("@/hooks/useCoverage", () => ({
  usePatientCoverage: vi.fn(),
}))

vi.mock("@/hooks/usePayments", () => ({
  usePatientBalance: vi.fn(),
}))

function TestPatientChartPage({ patientId }: { patientId: string }) {
  const { data: patient, isLoading, error } = usePatients.usePatient(patientId)

  if (isLoading) {
    return <div data-testid="loading">Loading patient details...</div>
  }

  if (error || !patient) {
    return <div data-testid="error">Patient not found</div>
  }

  return (
    <div data-testid="patient-chart">
      <PatientSummary patient={patient} />
    </div>
  )
}

describe("PatientChartPage Integration", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(useCoverage, "usePatientCoverage").mockReturnValue({
      data: undefined,
    } as any)
    vi.spyOn(usePayments, "usePatientBalance").mockReturnValue({
      data: undefined,
    } as any)
  })

  it("shows loading state while fetching the patient", () => {
    vi.spyOn(usePatients, "usePatient").mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as any)

    renderWithProviders(<TestPatientChartPage patientId="patient-a" />)

    expect(screen.getByTestId("loading")).toBeInTheDocument()
  })

  it("shows an error state when the patient fetch fails", () => {
    vi.spyOn(usePatients, "usePatient").mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("Network error"),
    } as any)

    renderWithProviders(<TestPatientChartPage patientId="patient-a" />)

    expect(screen.getByTestId("error")).toBeInTheDocument()
  })

  it("renders demographics from a mocked API", () => {
    const patient = createMockPatient({
      id: "patient-a",
      first_name: "Alice",
      last_name: "Anders",
      email: "alice@example.com",
      phone: "555-0100",
      date_of_birth: "1990-01-01",
    })
    vi.spyOn(usePatients, "usePatient").mockReturnValue({
      data: patient,
      isLoading: false,
      error: null,
    } as any)

    renderWithProviders(<TestPatientChartPage patientId="patient-a" />)

    expect(screen.getByText("Alice Anders")).toBeInTheDocument()
    expect(screen.getByText("alice@example.com")).toBeInTheDocument()
    expect(screen.getByText("555-0100")).toBeInTheDocument()
  })

  it("never renders another patient's data on id change", () => {
    const patientA = createMockPatient({
      id: "patient-a",
      first_name: "Alice",
      last_name: "Anders",
      email: "alice@example.com",
    })
    const patientB = createMockPatient({
      id: "patient-b",
      first_name: "Bob",
      last_name: "Brown",
      email: "bob@example.com",
    })

    vi.spyOn(usePatients, "usePatient").mockImplementation((id: string) =>
      ({
        data: id === "patient-a" ? patientA : patientB,
        isLoading: false,
        error: null,
      }) as any,
    )

    const { rerender } = renderWithProviders(
      <TestPatientChartPage patientId="patient-a" />,
    )

    expect(screen.getByText("Alice Anders")).toBeInTheDocument()
    expect(screen.getByText("alice@example.com")).toBeInTheDocument()

    rerender(<TestPatientChartPage patientId="patient-b" />)

    expect(screen.queryByText("Alice Anders")).not.toBeInTheDocument()
    expect(screen.queryByText("alice@example.com")).not.toBeInTheDocument()
    expect(screen.getByText("Bob Brown")).toBeInTheDocument()
    expect(screen.getByText("bob@example.com")).toBeInTheDocument()
  })
})

describe("PatientChartPage composition", () => {
  /**
   * Asserted against the page source, not a render.
   *
   * The wrapper above stands in for the page because the real one resolves
   * an async `params` promise that needs a full Next.js environment. A
   * wrapper cannot prove what the page composes, and the extension slot is
   * a component that renders nothing, so there is no DOM order to compare
   * either. Reading the file is what is left, and it is what the claim is
   * about: the order the page puts them in.
   */
  const source = readFileSync(join(__dirname, "..", "page.tsx"), "utf8")

  it("renders the intake card above the extension slot", () => {
    const intakeAt = source.indexOf("<IntakeCard")
    const extrasAt = source.indexOf("<PatientChartExtras")

    expect(intakeAt).toBeGreaterThan(-1)
    expect(extrasAt).toBeGreaterThan(-1)
    expect(intakeAt).toBeLessThan(extrasAt)
  })

  it("passes the patient's id to the intake card", () => {
    expect(source).toContain("<IntakeCard patientId={patient.id} />")
  })
})
