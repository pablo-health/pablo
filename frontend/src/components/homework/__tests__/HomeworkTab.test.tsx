// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * HomeworkTab: assignments show done or not yet with the client's note and a
 * running count, open ones first; a withdrawn one is gone; Remove withdraws
 * and a reword sends the new words; the portal line appears only when the
 * practice's portal and its homework part are both on; and an empty add is
 * flagged, not sent. Suggestions are chart updates and never render here.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import type { HomeworkAssignment, HomeworkOverview } from "@/types/homework"
import { HomeworkTab } from "../HomeworkTab"

let overview: HomeworkOverview
let portal: { enabled: boolean; modules: Record<string, boolean> } | undefined
const withdraw = vi.fn()
const add = vi.fn()
const edit = vi.fn()
const showToast = vi.fn()

const mutation = (mutateAsync: typeof add) => ({ mutateAsync, isPending: false })

vi.mock("@/hooks/useHomework", () => ({
  usePatientHomework: () => ({ data: overview, isPending: false, error: null }),
  useWithdrawHomework: () => mutation(withdraw),
  useAddHomework: () => mutation(add),
  useEditHomework: () => mutation(edit),
}))

vi.mock("@/hooks/usePortalSettings", () => ({
  usePortalSettings: () => ({ data: portal }),
}))

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ showToast }),
}))

function item(overrides: Partial<HomeworkAssignment>): HomeworkAssignment {
  return {
    id: "h1",
    note_id: "n1",
    source_field: "plan.homework_assignments",
    proposed_text: null,
    text: "Complete two thought records",
    status: "assigned",
    origin: "note",
    assigned_at: "2026-10-10T15:00:00Z",
    completed_at: null,
    client_note: null,
    created_at: "2026-10-10T15:00:00Z",
    ...overrides,
  }
}

describe("HomeworkTab", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    portal = undefined
    overview = { assignments: [] }
  })

  it("lists open work first, counts what is done, shows the client's note, hides withdrawn", () => {
    overview.assignments = [
      item({
        id: "done",
        text: "Walk after dinner",
        status: "done",
        assigned_at: "2026-10-02T15:00:00Z",
        completed_at: "2026-10-05T15:00:00Z",
        client_note: "Three evenings this week",
      }),
      item({ id: "open", text: "Read chapter 2", assigned_at: "2026-10-01T15:00:00Z" }),
      item({ id: "gone", text: "Old task", status: "withdrawn" }),
    ]
    render(<HomeworkTab patientId="p1" />)

    const rows = screen.getAllByTestId("homework-assignment")
    expect(rows.map((r) => r.getAttribute("data-status"))).toEqual(["assigned", "done"])
    expect(screen.queryByText("Old task")).not.toBeInTheDocument()
    expect(screen.getByTestId("homework-progress")).toHaveTextContent("1 of 2 done")
    expect(within(rows[0]).getByText("Not done yet")).toBeInTheDocument()
    expect(within(rows[1]).getByText(/^Done /)).toBeInTheDocument()
    expect(screen.getByTestId("homework-client-note")).toHaveTextContent(
      "Three evenings this week",
    )
  })

  it("withdraws with Remove and rewords an open one", async () => {
    overview.assignments = [item({})]
    render(<HomeworkTab patientId="p1" />)

    fireEvent.click(screen.getByRole("button", { name: "Edit" }))
    fireEvent.change(screen.getByLabelText("Edit"), { target: { value: "One record a day" } })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() =>
      expect(edit).toHaveBeenCalledWith({
        patientId: "p1",
        assignmentId: "h1",
        text: "One record a day",
      }),
    )

    fireEvent.click(screen.getByRole("button", { name: "Remove" }))
    await waitFor(() => expect(withdraw).toHaveBeenCalledWith({ patientId: "p1", assignmentId: "h1" }))
  })

  it("says the portal shows homework only when the practice turned it on", () => {
    overview.assignments = [item({})]
    portal = { enabled: true, modules: { homework: false } }
    const { rerender } = render(<HomeworkTab patientId="p1" />)
    expect(screen.queryByText(/see assigned homework in the portal/)).not.toBeInTheDocument()

    portal = { enabled: true, modules: { homework: true } }
    rerender(<HomeworkTab patientId="p1" />)
    expect(screen.getByText("Clients see assigned homework in the portal.")).toBeInTheDocument()
  })

  it("flags an empty assignment instead of sending it, and adds a written one", async () => {
    render(<HomeworkTab patientId="p1" />)
    expect(screen.getByText("No homework assigned.")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Add homework" }))
    expect(screen.getByText("Write what to do before you add it.")).toBeInTheDocument()
    expect(add).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText("Add homework"), {
      target: { value: "  Keep a sleep diary  " },
    })
    fireEvent.click(screen.getByRole("button", { name: "Add homework" }))
    await waitFor(() =>
      expect(add).toHaveBeenCalledWith({ patientId: "p1", text: "Keep a sleep diary" }),
    )
  })
})
