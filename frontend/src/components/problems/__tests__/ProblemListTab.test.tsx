// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ProblemListTab Component Tests
 *
 * The list reads as recorded (label, code or "No code", status), an empty
 * list says so, adding validates only the code's shape, and resolving,
 * reordering and removing send the right calls.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { ProblemListTab } from "../ProblemListTab"
import { ApiError } from "@/lib/api/client"
import type { Problem } from "@/types/problems"

let problems: Problem[] = []
const addProblem = vi.fn()
const updateProblem = vi.fn()
const reorderProblems = vi.fn()
const removeProblem = vi.fn()
const showToast = vi.fn()

const mutation = (mutateAsync: typeof addProblem) => ({ mutateAsync, isPending: false })

vi.mock("@/hooks/useProblems", () => ({
  usePatientProblems: () => ({
    data: { data: problems, total: problems.length },
    isLoading: false,
    error: null,
  }),
  useAddProblem: () => mutation(addProblem),
  useUpdateProblem: () => mutation(updateProblem),
  useReorderProblems: () => mutation(reorderProblems),
  useRemoveProblem: () => mutation(removeProblem),
}))

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ showToast }),
}))

function problem(overrides: Partial<Problem>): Problem {
  return {
    id: "p1",
    patient_id: "patient_1",
    label: "Generalized anxiety disorder",
    icd10_code: "F41.1",
    status: "active",
    onset_date: null,
    position: 0,
    source_note_id: null,
    added_by: "user_1",
    added_at: "2026-10-01T00:00:00Z",
    resolved_at: null,
    updated_at: "2026-10-01T00:00:00Z",
    ...overrides,
  }
}

describe("ProblemListTab", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    problems = []
  })

  it("says when no diagnoses are recorded", () => {
    render(<ProblemListTab patientId="patient_1" />)
    expect(screen.getByText("No diagnoses recorded.")).toBeInTheDocument()
    expect(screen.getByLabelText("Diagnosis")).toBeInTheDocument()
  })

  it("lists each problem with its code or no code, and its status", () => {
    problems = [
      problem({}),
      problem({ id: "p2", label: "Insomnia", icd10_code: null, position: 1 }),
      problem({ id: "p3", label: "PTSD", icd10_code: "F43.10", status: "resolved" }),
    ]
    render(<ProblemListTab patientId="patient_1" />)

    const rows = screen.getAllByTestId("problem-row")
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent("Generalized anxiety disorderF41.1Active")
    expect(rows[1]).toHaveTextContent("No code")
    expect(rows[2]).toHaveTextContent("Resolved")
    expect(screen.getByRole("button", { name: "Reactivate" })).toBeInTheDocument()
  })

  it("adds a diagnosis with an upper-cased code and its status", async () => {
    addProblem.mockResolvedValue(problem({}))
    render(<ProblemListTab patientId="patient_1" />)

    fireEvent.change(screen.getByLabelText("Diagnosis"), {
      target: { value: "Generalized anxiety disorder" },
    })
    fireEvent.change(screen.getByLabelText("ICD-10 code"), { target: { value: "f41.1" } })
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "rule_out" } })
    fireEvent.click(screen.getByRole("button", { name: "Add" }))

    await waitFor(() =>
      expect(addProblem).toHaveBeenCalledWith({
        patientId: "patient_1",
        data: { label: "Generalized anxiety disorder", icd10_code: "F41.1", status: "rule_out" },
      }),
    )
  })

  it("adds a diagnosis with no code", async () => {
    addProblem.mockResolvedValue(problem({}))
    render(<ProblemListTab patientId="patient_1" />)

    fireEvent.change(screen.getByLabelText("Diagnosis"), { target: { value: "Insomnia" } })
    fireEvent.click(screen.getByRole("button", { name: "Add" }))

    await waitFor(() =>
      expect(addProblem).toHaveBeenCalledWith({
        patientId: "patient_1",
        data: { label: "Insomnia", icd10_code: null, status: "active" },
      }),
    )
  })

  it("refuses something that is not shaped like a code", async () => {
    render(<ProblemListTab patientId="patient_1" />)

    fireEvent.change(screen.getByLabelText("Diagnosis"), { target: { value: "Anxiety" } })
    fireEvent.change(screen.getByLabelText("ICD-10 code"), { target: { value: "anxiety" } })
    fireEvent.click(screen.getByRole("button", { name: "Add" }))

    expect(await screen.findByText("Enter a code like F41.1, or leave it blank.")).toBeInTheDocument()
    expect(addProblem).not.toHaveBeenCalled()
  })

  it("says so when the diagnosis is already listed", async () => {
    addProblem.mockRejectedValue(new ApiError("PROBLEM_ALREADY_LISTED", "listed", {}, 409))
    render(<ProblemListTab patientId="patient_1" />)

    fireEvent.change(screen.getByLabelText("Diagnosis"), { target: { value: "GAD" } })
    fireEvent.click(screen.getByRole("button", { name: "Add" }))

    await waitFor(() =>
      expect(showToast).toHaveBeenCalledWith("That diagnosis is already on the list.", "error"),
    )
  })

  it("resolves, reorders within a status, and removes", async () => {
    problems = [problem({}), problem({ id: "p2", label: "MDD", icd10_code: "F33.1", position: 1 })]
    vi.stubGlobal("confirm", vi.fn(() => true))
    render(<ProblemListTab patientId="patient_1" />)

    expect(screen.getByRole("button", { name: "Move Generalized anxiety disorder up" })).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "Move MDD up" }))
    await waitFor(() =>
      expect(reorderProblems).toHaveBeenCalledWith({
        patientId: "patient_1",
        problemIds: ["p2", "p1"],
      }),
    )

    fireEvent.click(screen.getAllByRole("button", { name: "Resolve" })[0])
    await waitFor(() =>
      expect(updateProblem).toHaveBeenCalledWith({
        patientId: "patient_1",
        problemId: "p1",
        data: { status: "resolved" },
      }),
    )

    fireEvent.click(screen.getByRole("button", { name: "Remove MDD" }))
    await waitFor(() =>
      expect(removeProblem).toHaveBeenCalledWith({ patientId: "patient_1", problemId: "p2" }),
    )
  })
})
