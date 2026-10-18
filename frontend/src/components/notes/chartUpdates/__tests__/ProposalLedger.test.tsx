// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The ledger groups a note's proposals under their chart section, accepts a
 * section at once, leaves an uncertain medication for its own row, and gives
 * every cited line a mark that lights it in the source pane.
 */

import { describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { ProposalLedger, groupBySection, splitLabel } from "../ProposalLedger"
import type { ChartProposal } from "@/types/chartProposals"

const { decide } = vi.hoisted(() => ({ decide: vi.fn() }))
vi.mock("@/hooks/useChartProposals", () => ({
  useDecideChartProposal: () => ({ mutateAsync: decide, isPending: false }),
}))

function proposal(overrides: Partial<ChartProposal>): ChartProposal {
  return {
    id: overrides.id ?? overrides.label ?? "p",
    field_key: "supports",
    item_key: "",
    label: "Social history and supports: Supports",
    editable: true,
    change: null,
    current_text: null,
    proposed_text: "Sister and a neighbor.",
    what_changed: "Stated this visit",
    evidence: [{ segment_id: 2, text: "Client: Update on supports: Sister and a neighbor." }],
    origin: "document",
    decision: "pending",
    decided_text: null,
    decided_by: null,
    decided_at: null,
    created_at: "2026-09-25T14:00:00Z",
    ...overrides,
  }
}

const supports = proposal({ id: "supports" })
const relationships = proposal({
  id: "relationships",
  field_key: "relationships",
  label: "Social history and supports: Relationships",
  proposed_text: "Married, two children.",
  evidence: [{ segment_id: 0, text: "Relationships: Married, two children." }],
})
const trauma = proposal({
  id: "trauma",
  field_key: "trauma_history",
  label: "Trauma history",
  proposed_text: "None reported.",
  evidence: [],
})
const uncertain = proposal({
  id: "med",
  field_key: "medications",
  label: "Medications: Start buspirone",
  editable: false,
  proposed_text: "buspirone 5 mg, twice daily",
  change: {
    action: "start",
    drug_name: "buspirone",
    dose: "5 mg",
    frequency: "twice daily",
    category: null,
    reason: null,
    heard_as: "bus-pyrone",
    name_uncertain: true,
  },
})

describe("splitLabel and groupBySection", () => {
  it("reads the section off a 'Section: Field' label and keeps a bare one whole", () => {
    expect(splitLabel("Social history and supports: Supports")).toEqual({
      section: "Social history and supports",
      field: "Supports",
    })
    expect(splitLabel("Trauma history")).toEqual({ section: "Trauma history", field: "Trauma history" })
  })

  it("groups in first-seen order", () => {
    expect(groupBySection([supports, trauma, relationships]).map((s) => s.name)).toEqual([
      "Social history and supports",
      "Trauma history",
    ])
  })
})

describe("ProposalLedger", () => {
  it("lists each section once with its rows under it", () => {
    render(
      <ProposalLedger
        noteId="n1"
        patientId="pt1"
        proposals={[supports, relationships, trauma]}
      />,
    )

    const social = screen.getByRole("region", { name: "Social history and supports" })
    expect(social.querySelectorAll('[data-testid="chart-proposal"]')).toHaveLength(2)
    expect(screen.getByRole("region", { name: "Trauma history" })).toBeInTheDocument()
    // The row keeps the full label for anyone finding it by name.
    expect(
      screen.getByRole("listitem", { name: "Social history and supports: Supports" }),
    ).toHaveTextContent("Supports")
  })

  it("accepts a section at once, in order, and leaves an uncertain medication alone", async () => {
    decide.mockResolvedValue({})
    render(
      <ProposalLedger
        noteId="n1"
        patientId="pt1"
        proposals={[supports, relationships, uncertain]}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Accept all Social history and supports" }))

    await waitFor(() => expect(decide).toHaveBeenCalledTimes(2))
    expect(decide.mock.calls.map((c) => c[0].proposalId)).toEqual(["supports", "relationships"])
    // One medication is one row: no "accept all" for its section, and it
    // still asks to be confirmed on its own.
    expect(screen.queryByRole("button", { name: "Accept all Medications" })).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Confirm and accept" })).toBeInTheDocument()
  })

  it("marks each cited line, and the mark lights it in the source pane", () => {
    const light = vi.fn()
    render(
      <ProposalLedger noteId="n1" patientId="pt1" proposals={[supports]} onEvidenceClick={light} />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Show source paragraph 3" }))

    expect(light).toHaveBeenCalledWith([2])
    expect(screen.getByText("Client: Update on supports: Sister and a neighbor.")).toBeInTheDocument()
  })

  it("shows the marks without a pane as plain numbers", () => {
    render(<ProposalLedger noteId="n1" patientId="pt1" proposals={[supports]} />)

    expect(screen.queryByRole("button", { name: /Show source paragraph/ })).not.toBeInTheDocument()
    expect(screen.getByText("¶3")).toBeInTheDocument()
  })
})

describe("ProposalLedger for an intake form", () => {
  const pronouns = proposal({
    id: "pronouns",
    field_key: "pronouns",
    label: "Name and pronouns: Pronouns",
    proposed_text: "they/them",
    what_changed: "Answered on the intake",
    origin: "intake",
    evidence: [
      { segment_id: 2, text: "they/them", item_id: "item-7", question: "What pronouns do you use?" },
    ],
  })

  it("decides through the form, not a note", async () => {
    decide.mockReset()
    decide.mockResolvedValue({})
    render(<ProposalLedger intakeAssignmentId="form-1" patientId="pt1" proposals={[pronouns]} />)

    fireEvent.click(screen.getByRole("button", { name: "Accept" }))

    await waitFor(() => expect(decide).toHaveBeenCalledTimes(1))
    expect(decide.mock.calls[0][0]).toMatchObject({
      intakeAssignmentId: "form-1",
      proposalId: "pronouns",
      data: { decision: "accept" },
    })
    expect(decide.mock.calls[0][0].noteId).toBeUndefined()
  })

  it("shows the answer under its question, and the question opens the form there", () => {
    const open = vi.fn()
    render(
      <ProposalLedger
        intakeAssignmentId="form-1"
        patientId="pt1"
        proposals={[pronouns]}
        onAnswerClick={open}
      />,
    )

    expect(screen.queryByText("¶3")).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "What pronouns do you use?" }))
    expect(open).toHaveBeenCalledWith("item-7")
  })
})
