// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What the clinician said in the session they would do, at sign and on the
 * signed note: its own section, never under "Update the chart"; the title and
 * date are theirs to change before it is added; dismissing files nothing.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"
import { FinalizeButton } from "@/components/sessions/FinalizeButton"
import * as useSessions from "@/hooks/useSessions"
import { decideChartProposal, getChartProposals } from "@/lib/api/chartProposals"
import { getNoteType } from "@/lib/api/noteTypes"
import { listProblems } from "@/lib/api/problems"
import { createMockNote } from "@/test/factories"
import type { ChartProposal, ProposalRun } from "@/types/chartProposals"
import type { NoteTypeSchema } from "@/types/noteTypes"
import { ChartUpdatesPanel } from "../ChartUpdatesPanel"
import { waitingText } from "../ChartUpdatesWaiting"

vi.mock("@/lib/api/chartProposals", () => ({
  getChartProposals: vi.fn(),
  decideChartProposal: vi.fn(),
  retryChartProposals: vi.fn(),
}))
vi.mock("@/lib/api/noteTypes", () => ({ getNoteType: vi.fn(), listNoteTypes: vi.fn() }))
vi.mock("@/lib/api/problems", () => ({ addProblem: vi.fn(), listProblems: vi.fn() }))
vi.mock("@/hooks/useNoteSigning", () => ({
  useSignerDefaults: () => ({ name: "Sam Ortiz", credentials: "LCSW", loaded: true }),
}))
vi.mock("@/hooks/usePreferences", () => ({ useUserTimeZone: () => "America/New_York" }))
vi.mock("@/lib/access/readOnlyMode", () => ({ useReadOnlyMode: () => ({ readOnly: false }) }))

const SOAP: NoteTypeSchema = {
  key: "soap",
  label: "SOAP",
  description: "",
  tier: "core",
  context: "session",
  inputs: [],
  version: null,
  sections: [],
} as unknown as NoteTypeSchema

const NOTE = createMockNote({ id: "note-1", patient_id: "patient-1", note_type: "soap" })
// A SOAP note's chart call does not run.
const SKIPPED: ProposalRun = { status: "skipped", computed_at: "2026-10-14T00:00:00Z", retryable: false }

function referral(overrides: Partial<ChartProposal> = {}): ChartProposal {
  return {
    id: "task-1",
    field_key: "session_task",
    item_key: "S2",
    label: "You said you would",
    editable: true,
    change: null,
    commitment: { due_date: "2026-10-16", due_said: "by Friday" },
    current_text: null,
    proposed_text: "Send the referral to Dr. Okafor",
    what_changed: "by Friday",
    evidence: [{ segment_id: 2, text: "[00:46] Therapist: I'll send a referral by Friday." }],
    origin: "transcript",
    decision: "pending",
    decided_text: null,
    decided_by: null,
    decided_at: null,
    created_at: "2026-10-14T00:00:00Z",
    ...overrides,
  }
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

async function openSignDialog() {
  const user = userEvent.setup()
  render(
    <FinalizeButton sessionId="session-1" status="pending_review" qualityRating={null} note={NOTE} />,
    { wrapper },
  )
  await user.click(screen.getByRole("button", { name: /sign and lock/i }))
  return { user, dialog: screen.getByRole("dialog") }
}

beforeEach(() => {
  vi.mocked(getNoteType).mockResolvedValue(SOAP)
  vi.mocked(listProblems).mockResolvedValue({ data: [], total: 0 })
  vi.mocked(getChartProposals).mockResolvedValue({ data: [referral()], run: SKIPPED })
  vi.spyOn(useSessions, "useFinalizeSession").mockReturnValue({
    mutateAsync: vi.fn(),
    isPending: false,
  } as never)
})

afterEach(() => vi.clearAllMocks())

describe("tasks from the session, at sign", () => {
  it("are their own section on a note that updates no chart", async () => {
    const { dialog } = await openSignDialog()

    const row = await within(dialog).findByTestId("session-task-proposal")
    expect(within(dialog).getByRole("heading", { name: "You said you would" })).toBeInTheDocument()
    expect(within(dialog).queryByText("Update the chart")).not.toBeInTheDocument()
    expect(within(row).getByRole("textbox", { name: "Task" })).toHaveValue(
      "Send the referral to Dr. Okafor",
    )
    expect(within(row).getByLabelText("Due date")).toHaveValue("2026-10-16")
    // The words the date was read from, beside the line they were said on.
    expect(within(row).getByText("You said").parentElement).toHaveTextContent("You said “by Friday”")
    // Each is work the clinician takes on: added one at a time, never in bulk.
    expect(within(dialog).queryByRole("button", { name: /Accept all/ })).not.toBeInTheDocument()
  })

  it("adds the task with the clinician's own title and date", async () => {
    vi.mocked(decideChartProposal).mockResolvedValue(referral({ decision: "edited" }))
    const { user, dialog } = await openSignDialog()

    const row = await within(dialog).findByTestId("session-task-proposal")
    const title = within(row).getByRole("textbox", { name: "Task" })
    await user.clear(title)
    await user.type(title, "Fax the psychiatry referral")
    await user.clear(within(row).getByLabelText("Due date"))
    await user.type(within(row).getByLabelText("Due date"), "2026-10-19")
    await user.click(within(row).getByRole("button", { name: "Add to my tasks" }))

    expect(decideChartProposal).toHaveBeenCalledWith("note-1", "task-1", {
      decision: "edit",
      text: "Fax the psychiatry referral",
      due_date: "2026-10-19",
    })
  })

  it("adds it as proposed, or undated when the date is cleared", async () => {
    vi.mocked(decideChartProposal).mockResolvedValue(referral({ decision: "accepted" }))
    const { user, dialog } = await openSignDialog()

    const row = await within(dialog).findByTestId("session-task-proposal")
    await user.clear(within(row).getByLabelText("Due date"))
    await user.click(within(row).getByRole("button", { name: "Add to my tasks" }))

    expect(decideChartProposal).toHaveBeenCalledWith("note-1", "task-1", {
      decision: "accept",
      due_date: null,
    })
  })

  it("dismisses one without filing anything", async () => {
    vi.mocked(decideChartProposal).mockResolvedValue(referral({ decision: "discarded" }))
    const { user, dialog } = await openSignDialog()

    const row = await within(dialog).findByTestId("session-task-proposal")
    await user.click(within(row).getByRole("button", { name: "Dismiss" }))

    expect(decideChartProposal).toHaveBeenCalledWith("note-1", "task-1", { decision: "discard" })
  })
})

describe("tasks from the session, on the signed note", () => {
  it("show apart from the chart updates, and say where an added one went", async () => {
    vi.mocked(getChartProposals).mockResolvedValue({
      data: [referral({ decision: "accepted" })],
      run: SKIPPED,
    })
    render(<ChartUpdatesPanel note={NOTE} />, { wrapper })

    const tasks = await screen.findByTestId("session-tasks")
    expect(within(tasks).getByText("Added to your tasks")).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByTestId("chart-updates")).not.toBeInTheDocument())
  })
})

describe("what the chart says is waiting", () => {
  it("names tasks apart from chart updates", () => {
    expect(waitingText(3, 1)).toBe("2 chart updates and 1 task")
    expect(waitingText(1, 1)).toBe("1 task")
    expect(waitingText(2, 0)).toBe("2 chart updates")
  })
})
