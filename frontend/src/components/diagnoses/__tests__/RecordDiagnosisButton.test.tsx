// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * RecordDiagnosisButton tests (PABLO-6xj)
 *
 * Covers the record on-ramp: definitions render as a picker, the criterion
 * checklist starts collapsed in the default "lite" depth, a clinician can save
 * a clinical impression by confirming just a code, and documenting criteria
 * drives a live determination. The API client is mocked — determination logic
 * is exercised separately in the evaluator unit test.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { RecordDiagnosisButton } from "../RecordDiagnosisButton"
import { ToastProvider } from "@/components/ui/Toast"
import * as api from "@/lib/api/diagnoses"
import type { DiagnosticDefinition } from "@/types/diagnoses"

vi.mock("@/lib/api/diagnoses")

const MDD: DiagnosticDefinition = {
  code: "mdd",
  version: 1,
  display_name: "Major Depressive Disorder",
  evaluator_type: "criteria",
  suggested_icd10: "F32.9",
  criterion_groups: [
    {
      key: "A",
      label: "Core symptoms",
      min_met: 5,
      require_cardinal: true,
      criteria: [
        { key: "A1", label: "Depressed mood", cardinal: true },
        { key: "A2", label: "Loss of interest", cardinal: true },
        { key: "A3", label: "Appetite change", cardinal: false },
        { key: "A4", label: "Sleep change", cardinal: false },
        { key: "A5", label: "Fatigue", cardinal: false },
      ],
    },
  ],
  gates: [{ key: "duration", label: "Present about two weeks" }],
  icd10_options: [
    { code: "F32.9", label: "MDD, single episode, unspecified" },
    { code: "F33.9", label: "MDD, recurrent, unspecified" },
  ],
}

const GAD: DiagnosticDefinition = {
  code: "gad",
  version: 1,
  display_name: "Generalized Anxiety Disorder",
  evaluator_type: "criteria",
  suggested_icd10: "F41.1",
  criterion_groups: [],
  gates: [],
  icd10_options: [{ code: "F41.1", label: "Generalized anxiety disorder" }],
}

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>{children}</ToastProvider>
    </QueryClientProvider>
  )
  Wrapper.displayName = "RecordDiagnosisWrapper"
  return Wrapper
}

async function openDialog() {
  const user = userEvent.setup()
  render(<RecordDiagnosisButton patientId="p1" />, { wrapper: createWrapper() })
  await user.click(screen.getByRole("button", { name: /record diagnosis/i }))
  // Wait for definitions to load and the picker to render.
  await screen.findByRole("button", { name: /major depressive disorder/i })
  return user
}

describe("RecordDiagnosisButton", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.listDiagnosticDefinitions).mockResolvedValue({
      data: [MDD, GAD],
      total: 2,
    })
    vi.mocked(api.createDiagnosticAssessment).mockResolvedValue({
      id: "d1",
    } as Awaited<ReturnType<typeof api.createDiagnosticAssessment>>)
  })

  it("shows the draft-criteria notice so wording isn't taken as vetted", async () => {
    await openDialog()
    expect(screen.getByText(/draft criteria/i)).toBeInTheDocument()
    expect(
      screen.getByText(/substitute for clinical judgment/i),
    ).toBeInTheDocument()
  })

  it("renders the definitions and starts with criteria collapsed (lite)", async () => {
    await openDialog()
    expect(
      screen.getByRole("button", { name: /generalized anxiety disorder/i }),
    ).toBeInTheDocument()
    // Collapsed: the toggle is present, criterion labels are not yet rendered.
    expect(screen.getByText(/document criteria \(optional\)/i)).toBeInTheDocument()
    expect(screen.queryByText(/depressed mood/i)).not.toBeInTheDocument()
  })

  it("saves a clinical impression with just a confirmed code", async () => {
    const user = await openDialog()
    // Save is disabled until a code (or met criteria) exists.
    expect(screen.getByRole("button", { name: /save diagnosis/i })).toBeDisabled()
    await user.click(screen.getByRole("button", { name: /^F32\.9$/ }))
    await user.click(screen.getByRole("button", { name: /save diagnosis/i }))

    await waitFor(() =>
      expect(api.createDiagnosticAssessment).toHaveBeenCalled(),
    )
    const [patientId, body] = vi.mocked(api.createDiagnosticAssessment).mock
      .calls[0]
    expect(patientId).toBe("p1")
    expect(body.instrument).toBe("mdd")
    expect(body.source).toBe("manual")
    expect(body.determined_icd10).toBe("F32.9")
    expect(body.criterion_responses).toEqual({})
  })

  it("documenting criteria drives a live determination and suggestion", async () => {
    const user = await openDialog()
    await user.click(screen.getByText(/document criteria \(optional\)/i))
    // Check 5 criteria incl. a cardinal, plus the gate.
    for (const name of [
      /depressed mood/i,
      /loss of interest/i,
      /appetite change/i,
      /sleep change/i,
      /fatigue/i,
      /present about two weeks/i,
    ]) {
      await user.click(screen.getByRole("button", { name }))
    }
    expect(screen.getByText(/criteria met/i)).toBeInTheDocument()
    expect(screen.getByText(/suggested: F32\.9/i)).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /save diagnosis/i }))
    await waitFor(() =>
      expect(api.createDiagnosticAssessment).toHaveBeenCalled(),
    )
    const [, body] = vi.mocked(api.createDiagnosticAssessment).mock.calls[0]
    expect(body.determined_icd10).toBe("F32.9")
    expect(body.criterion_responses).toMatchObject({ A1: true, A3: true })
    expect(body.gate_responses).toMatchObject({ duration: true })
  })
})
