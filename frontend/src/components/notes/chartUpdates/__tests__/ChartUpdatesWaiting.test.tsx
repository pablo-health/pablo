// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The chart's strip of waiting updates names where they came from, heads an
 * accepted intake form's updates with the day it was handed in, and opens
 * the form at the answer an update cites.
 */

import { describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import { ChartUpdatesWaiting, sourcesText } from "../ChartUpdatesWaiting"
import type { PatientChartProposalsResponse } from "@/types/chartProposals"

const { listing } = vi.hoisted(() => ({
  listing: { data: undefined as PatientChartProposalsResponse | undefined },
}))
vi.mock("@/hooks/useChartProposals", () => ({
  usePatientChartProposals: () => listing,
  useDecideChartProposal: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock("@/hooks/useNoteTypes", () => ({ useNoteTypeLabel: () => (t: string) => t }))
vi.mock("@/hooks/useProblems", () => ({ usePatientProblems: () => ({ data: undefined }) }))
vi.mock("@/lib/access/readOnlyMode", () => ({ useReadOnlyMode: () => ({ readOnly: false }) }))
vi.mock("@/components/patients/IntakeReviewPanel", () => ({
  IntakeReviewPanel: (props: { assignmentId: string; focusItemId?: string }) => (
    <p data-testid="opened-form">
      {props.assignmentId} at {props.focusItemId ?? "the top"}
    </p>
  ),
}))

const intakeOnly: PatientChartProposalsResponse = {
  groups: [],
  intakes: [
    {
      assignment_id: "form-1",
      packet_name: "New client paperwork",
      date: "2026-10-15T15:00:00Z",
      proposals: [
        {
          id: "contact",
          field_key: "emergency_contact",
          item_key: "",
          label: "Contacts: Emergency contact",
          editable: true,
          change: null,
          current_text: null,
          proposed_text: "Jordan Lee (sister), 555-0100",
          what_changed: "Answered on the intake",
          evidence: [
            {
              segment_id: 3,
              text: "Jordan Lee (sister), 555-0100",
              item_id: "item-contact",
              question: "Who should we contact in an emergency?",
            },
          ],
          origin: "intake",
          decision: "pending",
          decided_text: null,
          decided_by: null,
          decided_at: null,
          created_at: "2026-10-16T09:00:00Z",
        },
      ],
    },
  ],
  pending: 1,
}

describe("sourcesText", () => {
  it("counts notes and intakes in words", () => {
    expect(sourcesText(2, 0)).toBe("2 notes")
    expect(sourcesText(0, 1)).toBe("1 intake")
    expect(sourcesText(1, 1)).toBe("1 note and 1 intake")
  })
})

describe("ChartUpdatesWaiting", () => {
  it("heads a form's updates with the day it came in", () => {
    listing.data = intakeOnly
    render(<ChartUpdatesWaiting patientId="pt1" />)

    expect(screen.getByText(/1 chart update waiting/)).toBeInTheDocument()
    expect(screen.getByText(/from 1 intake/)).toBeInTheDocument()
    expect(screen.getByTestId("chart-updates-intake-group")).toHaveTextContent(
      "From the intake on October 15, 2026, New client paperwork",
    )
  })

  it("opens the form at the answer an update cites", () => {
    listing.data = intakeOnly
    render(<ChartUpdatesWaiting patientId="pt1" />)

    fireEvent.click(screen.getByRole("button", { name: "Who should we contact in an emergency?" }))

    expect(screen.getByTestId("opened-form")).toHaveTextContent("form-1 at item-contact")
  })

  it("shows nothing when nothing is waiting", () => {
    listing.data = { groups: [], intakes: [], pending: 0 }
    const { container } = render(<ChartUpdatesWaiting patientId="pt1" />)
    expect(container).toBeEmptyDOMElement()
  })
})
