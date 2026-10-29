// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * "Add to problem list" beside each diagnosis a note states: it sends the
 * diagnosis as stated, and shows when the chart already has it.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"
import { NoteViewer } from "../NoteViewer"
import { isListed, problemStatusFor } from "../AddToProblemList"
import { getNoteType } from "@/lib/api/noteTypes"
import { addProblem, listProblems } from "@/lib/api/problems"
import { ApiError } from "@/lib/api/client"
import { createMockNote } from "@/test/factories"
import type { NoteTypeSchema } from "@/types/noteTypes"
import type { Problem } from "@/types/problems"

vi.mock("@/lib/api/noteTypes", () => ({ getNoteType: vi.fn(), listNoteTypes: vi.fn() }))
vi.mock("@/lib/api/problems", () => ({ addProblem: vi.fn(), listProblems: vi.fn() }))
vi.mock("@/lib/utils/pdfExport", () => ({ exportSOAPToPDF: vi.fn() }))
vi.mock("@/lib/config", () => ({ useConfig: () => ({ showVerificationBadges: true }) }))

const readOnly = vi.hoisted(() => ({ value: false }))
vi.mock("@/lib/access/readOnlyMode", () => ({ useReadOnlyMode: () => ({ readOnly: readOnly.value }) }))

const EVALUATION: NoteTypeSchema = {
  key: "custom.evaluation",
  label: "Evaluation",
  description: "",
  tier: "core",
  context: "session",
  inputs: [],
  version: 1,
  sections: [
    {
      key: "assessment",
      label: "Assessment",
      fields: [{ key: "diagnoses", label: "Diagnoses", kind: "diagnoses", ai_hint: "" }],
    },
  ],
}

const NOTE = createMockNote({
  id: "note-9",
  patient_id: "patient-7",
  note_type: "custom.evaluation",
  note_type_version: 1,
  content: {
    assessment: {
      diagnoses: [
        { label: "Generalized anxiety disorder", code: "F41.1", status: null },
        { label: "ADHD, inattentive", code: "F90.0", status: "Rule out" },
      ],
    },
  },
})

function problem(overrides: Partial<Problem>): Problem {
  return {
    id: "problem-1",
    patient_id: "patient-7",
    label: "",
    icd10_code: null,
    status: "active",
    onset_date: null,
    position: 0,
    source_note_id: null,
    added_by: null,
    added_at: "2026-10-06T00:00:00Z",
    resolved_at: null,
    updated_at: "2026-10-06T00:00:00Z",
    ...overrides,
  }
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

async function rowFor(label: string) {
  return (await screen.findByText(new RegExp(label))).closest("li") as HTMLElement
}

beforeEach(() => {
  readOnly.value = false
  vi.mocked(getNoteType).mockResolvedValue(EVALUATION)
  vi.mocked(listProblems).mockResolvedValue({ data: [], total: 0 })
})

afterEach(() => vi.clearAllMocks())

describe("add a stated diagnosis to the problem list", () => {
  it("sends the diagnosis as stated, with its note, and then shows it is listed", async () => {
    vi.mocked(addProblem).mockResolvedValue(problem({ label: "ADHD, inattentive" }))
    render(<NoteViewer note={NOTE} readonly />, { wrapper })

    fireEvent.click(
      await screen.findByRole("button", { name: "Add ADHD, inattentive to problem list" }),
    )

    await waitFor(() => expect(addProblem).toHaveBeenCalled())
    expect(vi.mocked(addProblem).mock.calls[0].slice(0, 2)).toEqual([
      "patient-7",
      {
        label: "ADHD, inattentive",
        icd10_code: "F90.0",
        status: "rule_out",
        source_note_id: "note-9",
      },
    ])
    expect(await within(await rowFor("ADHD")).findByText("On problem list")).toBeInTheDocument()
    expect(
      within(await rowFor("Generalized anxiety")).getByRole("button", { name: /Add .* to problem list/ }),
    ).toBeInTheDocument()
  })

  it("shows a diagnosis the chart already has as listed, with nothing to press", async () => {
    vi.mocked(listProblems).mockResolvedValue({
      data: [problem({ label: "GAD", icd10_code: "f41.1" })],
      total: 1,
    })
    render(<NoteViewer note={NOTE} readonly />, { wrapper })

    expect(await within(await rowFor("Generalized anxiety")).findByText("On problem list")).toBeInTheDocument()
    expect(screen.getAllByRole("button", { name: /to problem list/ })).toHaveLength(1)
  })

  it("treats 'already listed' from the server as listed", async () => {
    vi.mocked(addProblem).mockRejectedValue(
      new ApiError("PROBLEM_ALREADY_LISTED", "already listed", { problem_id: "p" }, 409),
    )
    render(<NoteViewer note={NOTE} readonly />, { wrapper })

    fireEvent.click(
      await screen.findByRole("button", { name: "Add Generalized anxiety disorder to problem list" }),
    )
    expect(await within(await rowFor("Generalized anxiety")).findByText("On problem list")).toBeInTheDocument()
  })

  it("says so when the stated code is not an ICD-10 code, and keeps the button", async () => {
    vi.mocked(addProblem).mockRejectedValue(new ApiError("VALIDATION_ERROR", "bad", {}, 422))
    render(<NoteViewer note={NOTE} readonly />, { wrapper })

    fireEvent.click(
      await screen.findByRole("button", { name: "Add Generalized anxiety disorder to problem list" }),
    )
    const row = await rowFor("Generalized anxiety")
    expect(await within(row).findByRole("alert")).toHaveTextContent("Not an ICD-10 code")
    expect(within(row).getByRole("button", { name: /to problem list/ })).toBeInTheDocument()
  })

  it("offers nothing to an account in read-only mode", async () => {
    readOnly.value = true
    render(<NoteViewer note={NOTE} />, { wrapper })

    expect(await screen.findByText(/Generalized anxiety disorder \(F41.1\)/)).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /to problem list/ })).not.toBeInTheDocument()
    expect(listProblems).not.toHaveBeenCalled()
  })
})

describe("matching and status", () => {
  it("records a stated rule-out as rule_out and anything else as active", () => {
    expect(problemStatusFor("rule out")).toBe("rule_out")
    expect(problemStatusFor("Rule-out")).toBe("rule_out")
    expect(problemStatusFor("working")).toBe("active")
    expect(problemStatusFor(null)).toBe("active")
  })

  it("matches by code when either has one, otherwise by label", () => {
    const coded = problem({ label: "Anything", icd10_code: "F41.1" })
    const uncoded = problem({ label: "Insomnia" })
    expect(isListed({ label: "GAD", code: "f41.1", status: null }, [coded])).toBe(true)
    expect(isListed({ label: "Anything", code: null, status: null }, [coded])).toBe(false)
    expect(isListed({ label: " insomnia ", code: null, status: null }, [uncoded])).toBe(true)
    expect(isListed({ label: "Insomnia", code: "G47.00", status: null }, [uncoded])).toBe(false)
  })
})
