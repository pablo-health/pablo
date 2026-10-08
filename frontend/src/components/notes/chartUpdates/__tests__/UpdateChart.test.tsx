// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The "Update the chart" step at sign, and the signed note's chart updates:
 * shown only when there is something to decide; accept, edit and discard
 * each send their decision; signing without updating writes nothing.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"
import { FinalizeButton } from "@/components/sessions/FinalizeButton"
import * as useSessions from "@/hooks/useSessions"
import {
  decideChartProposal,
  getChartProposals,
  retryChartProposals,
} from "@/lib/api/chartProposals"
import { getNoteType } from "@/lib/api/noteTypes"
import { listProblems } from "@/lib/api/problems"
import { createMockNote } from "@/test/factories"
import type { ChartProposal, MedicationChange, ProposalRun } from "@/types/chartProposals"
import type { NoteTypeSchema } from "@/types/noteTypes"
import { ChartUpdatesPanel } from "../ChartUpdatesPanel"
import { changeParts } from "../changeHighlight"

vi.mock("@/lib/api/chartProposals", () => ({
  getChartProposals: vi.fn(),
  decideChartProposal: vi.fn(),
  retryChartProposals: vi.fn(),
}))
vi.mock("@/lib/api/noteTypes", () => ({ getNoteType: vi.fn(), listNoteTypes: vi.fn() }))
vi.mock("@/lib/api/problems", () => ({ addProblem: vi.fn(), listProblems: vi.fn() }))
vi.mock("@/hooks/useNoteSigning", () => ({
  useSignerDefaults: () => ({ name: "Sam Ortiz", credentials: "MD" }),
}))
vi.mock("@/hooks/usePreferences", () => ({ useUserTimeZone: () => "America/New_York" }))
const readOnly = vi.hoisted(() => ({ value: false }))
vi.mock("@/lib/access/readOnlyMode", () => ({
  useReadOnlyMode: () => ({ readOnly: readOnly.value }),
}))

const SEPARATED = "Married; separated, divorce in progress since June."
const FINALIZED = "Married; separated, divorce in progress since June. Divorce finalized April 2."

const FOLLOW_UP: NoteTypeSchema = {
  key: "custom.follow_up",
  label: "Follow-up",
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
  id: "note-1",
  patient_id: "patient-1",
  note_type: "custom.follow_up",
  note_type_version: 1,
  content: { assessment: { diagnoses: [] } },
})

function proposal(overrides: Partial<ChartProposal> = {}): ChartProposal {
  return {
    id: "proposal-1",
    field_key: "relationships",
    item_key: "",
    label: "Social history and supports: Relationships",
    editable: true,
    change: null,
    current_text: SEPARATED,
    proposed_text: FINALIZED,
    what_changed: "Divorce finalized",
    evidence: [{ segment_id: 4, text: "[00:31] Client: The divorce was finalized on April 2." }],
    origin: "transcript",
    decision: "pending",
    decided_text: null,
    decided_by: null,
    decided_at: null,
    created_at: "2026-10-08T00:00:00Z",
    ...overrides,
  }
}

const OK: ProposalRun = { status: "ok", computed_at: "2026-10-08T00:00:00Z", retryable: true }
const FAILED: ProposalRun = { ...OK, status: "failed" }

function listing(data: ChartProposal[], run: ProposalRun | null = OK) {
  return { data, run }
}

const NOT_CHECKED = "Pablo couldn't check this note for chart updates."

const finalize = vi.fn()

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
  readOnly.value = false
  vi.mocked(getNoteType).mockResolvedValue(FOLLOW_UP)
  vi.mocked(listProblems).mockResolvedValue({ data: [], total: 0 })
  vi.mocked(getChartProposals).mockResolvedValue(listing([proposal()]))
  vi.spyOn(useSessions, "useFinalizeSession").mockReturnValue({
    mutateAsync: finalize,
    isPending: false,
  } as never)
})

afterEach(() => vi.clearAllMocks())

describe("the Update the chart step at sign", () => {
  it("does not appear when the note proposes nothing", async () => {
    vi.mocked(getChartProposals).mockResolvedValue(listing([]))
    const { dialog } = await openSignDialog()

    await waitFor(() => expect(getChartProposals).toHaveBeenCalledWith("note-1"))
    expect(within(dialog).queryByText("Update the chart")).not.toBeInTheDocument()
    expect(within(dialog).getByRole("button", { name: "Sign and lock" })).toBeInTheDocument()
  })

  it("shows the chart now, the proposed text with the change marked, and what was said", async () => {
    const { dialog } = await openSignDialog()

    const row = await within(dialog).findByRole("listitem", {
      name: "Social history and supports: Relationships",
    })
    expect(within(row).getByText("On the chart now").nextElementSibling).toHaveTextContent(
      SEPARATED,
    )
    expect(within(row).getByText("Divorce finalized")).toBeInTheDocument()
    expect(row.querySelector("mark")).toHaveTextContent("Divorce finalized April 2.")
    expect(within(row).getByText(/The divorce was finalized on April 2/)).toBeInTheDocument()
  })

  it("signs without updating in one click, writing nothing to the chart", async () => {
    const { user, dialog } = await openSignDialog()

    await user.click(await within(dialog).findByRole("button", { name: "Sign without updating" }))

    await waitFor(() => expect(finalize).toHaveBeenCalled())
    expect(decideChartProposal).not.toHaveBeenCalled()
  })

  it("accepts a proposal, then offers plain signing once nothing is left", async () => {
    vi.mocked(decideChartProposal).mockResolvedValue(proposal({ decision: "accepted" }))
    const { user, dialog } = await openSignDialog()

    vi.mocked(getChartProposals).mockResolvedValue(listing([proposal({ decision: "accepted" })]))
    await user.click(await within(dialog).findByRole("button", { name: "Accept" }))

    expect(decideChartProposal).toHaveBeenCalledWith("note-1", "proposal-1", { decision: "accept" })
    expect(await within(dialog).findByRole("button", { name: "Sign and lock" })).toBeInTheDocument()
    expect(within(dialog).queryByText("Update the chart")).not.toBeInTheDocument()
  })

  it("edits a proposal before it goes on the chart", async () => {
    vi.mocked(decideChartProposal).mockResolvedValue(proposal({ decision: "edited" }))
    const { user, dialog } = await openSignDialog()

    await user.click(await within(dialog).findByRole("button", { name: "Edit" }))
    const box = within(dialog).getByRole("textbox", { name: /Edit Social history/ })
    await user.clear(box)
    await user.type(box, "Divorced April 2.")
    await user.click(within(dialog).getByRole("button", { name: "Save to chart" }))

    expect(decideChartProposal).toHaveBeenCalledWith("note-1", "proposal-1", {
      decision: "edit",
      text: "Divorced April 2.",
    })
  })

  it("discards a proposal", async () => {
    vi.mocked(decideChartProposal).mockResolvedValue(proposal({ decision: "discarded" }))
    const { user, dialog } = await openSignDialog()

    await user.click(await within(dialog).findByRole("button", { name: "Discard" }))

    expect(decideChartProposal).toHaveBeenCalledWith("note-1", "proposal-1", {
      decision: "discard",
    })
  })

  it("offers a diagnosis the note states that the problem list lacks", async () => {
    vi.mocked(getChartProposals).mockResolvedValue(listing([]))
    vi.mocked(getNoteType).mockResolvedValue(FOLLOW_UP)
    const note = createMockNote({
      ...NOTE,
      content: { assessment: { diagnoses: [{ label: "Insomnia", code: "G47.00", status: null }] } },
    })
    const user = userEvent.setup()
    render(
      <FinalizeButton sessionId="session-1" status="pending_review" qualityRating={null} note={note} />,
      { wrapper },
    )
    await user.click(screen.getByRole("button", { name: /sign and lock/i }))
    const dialog = screen.getByRole("dialog")

    expect(
      await within(dialog).findByRole("button", { name: "Add Insomnia to problem list" }),
    ).toBeInTheDocument()
    expect(within(dialog).getByRole("button", { name: "Sign without updating" })).toBeInTheDocument()
  })
})

describe("a check that failed", () => {
  it("says so at sign, retries, and signs in one click either way", async () => {
    vi.mocked(getChartProposals).mockResolvedValue(listing([], FAILED))
    vi.mocked(retryChartProposals).mockResolvedValue(listing([proposal()]))
    const { user, dialog } = await openSignDialog()

    expect(await within(dialog).findByText(NOT_CHECKED)).toBeInTheDocument()
    expect(within(dialog).getByRole("button", { name: "Sign and lock" })).toBeInTheDocument()

    vi.mocked(getChartProposals).mockResolvedValue(listing([proposal()]))
    await user.click(within(dialog).getByRole("button", { name: "Retry" }))

    expect(retryChartProposals).toHaveBeenCalledWith("note-1")
    expect(
      await within(dialog).findByRole("button", { name: "Sign without updating" }),
    ).toBeInTheDocument()
    expect(within(dialog).queryByText(NOT_CHECKED)).not.toBeInTheDocument()
  })

  it("offers no retry where the note cannot be checked again", async () => {
    vi.mocked(getChartProposals).mockResolvedValue(listing([], { ...FAILED, retryable: false }))
    const { dialog } = await openSignDialog()

    expect(await within(dialog).findByText(NOT_CHECKED)).toBeInTheDocument()
    expect(within(dialog).queryByRole("button", { name: "Retry" })).not.toBeInTheDocument()
  })

  it("says nothing for a note type that is not checked", async () => {
    vi.mocked(getChartProposals).mockResolvedValue(listing([], { ...OK, status: "skipped" }))
    const { dialog } = await openSignDialog()

    await waitFor(() => expect(getChartProposals).toHaveBeenCalled())
    expect(within(dialog).queryByText(NOT_CHECKED)).not.toBeInTheDocument()
    expect(within(dialog).queryByText("Update the chart")).not.toBeInTheDocument()
  })

  it("is said on the signed note too", async () => {
    vi.mocked(getChartProposals).mockResolvedValue(listing([], FAILED))
    render(<ChartUpdatesPanel note={NOTE} />, { wrapper })

    expect(await screen.findByText(NOT_CHECKED)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument()
  })
})

describe("the signed note's chart updates", () => {
  it("lists what was decided and still offers what was left", async () => {
    vi.mocked(getChartProposals).mockResolvedValue(
      listing([
        proposal({ id: "a", decision: "discarded" }),
        proposal({ id: "b", label: "Trauma history", current_text: null, proposed_text: "None." }),
      ]),
    )
    render(<ChartUpdatesPanel note={NOTE} />, { wrapper })

    expect(await screen.findByText("Discarded")).toBeInTheDocument()
    const left = screen.getByRole("listitem", { name: "Trauma history" })
    expect(within(left).getByText("Not recorded")).toBeInTheDocument()
    expect(within(left).getByRole("button", { name: "Accept" })).toBeInTheDocument()
  })

  it("offers nothing to an account in read-only mode", async () => {
    readOnly.value = true
    render(<ChartUpdatesPanel note={NOTE} />, { wrapper })

    expect(await screen.findByTestId("proposed-text")).toHaveTextContent(FINALIZED)
    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument()
  })

  it("is not shown when the note proposed nothing", async () => {
    vi.mocked(getChartProposals).mockResolvedValue(listing([]))
    render(<ChartUpdatesPanel note={NOTE} />, { wrapper })

    await waitFor(() => expect(getChartProposals).toHaveBeenCalled())
    expect(screen.queryByText("Chart updates from this note")).not.toBeInTheDocument()
  })
})

function medication(
  action: MedicationChange["action"],
  name: string,
  overrides: Partial<ChartProposal> = {},
): ChartProposal {
  const verb = { start: "Start", stop: "Stop", change: "Change", add: "Add" }[action]
  return proposal({
    id: `${action}-${name}`,
    field_key: "medications",
    item_key: name,
    label: `Medications: ${verb} ${name}`,
    editable: false,
    change: {
      action,
      drug_name: name,
      dose: null,
      frequency: null,
      category: null,
      reason: null,
    },
    ...overrides,
  })
}

const START = medication("start", "hydroxyzine", {
  current_text: null,
  proposed_text: "hydroxyzine 25 mg, in the afternoon as needed",
  what_changed: "Started for afternoon anxiety",
})
const STOP = medication("stop", "trazodone", {
  current_text: "trazodone 50 mg, at bedtime",
  proposed_text: "Stopped: nausea",
  what_changed: "Stopped because of nausea",
})

describe("medication changes", () => {
  it("shows each against the list, accepted or discarded, never rewritten", async () => {
    vi.mocked(getChartProposals).mockResolvedValue(listing([START, STOP]))
    vi.mocked(decideChartProposal).mockResolvedValue({ ...START, decision: "accepted" })
    const { user, dialog } = await openSignDialog()

    const start = await within(dialog).findByRole("listitem", {
      name: "Medications: Start hydroxyzine",
    })
    const stop = within(dialog).getByRole("listitem", { name: "Medications: Stop trazodone" })
    expect(within(start).getByText("Not on the list")).toBeInTheDocument()
    expect(within(start).getByTestId("proposed-text")).toHaveTextContent(
      "hydroxyzine 25 mg, in the afternoon as needed",
    )
    expect(within(stop).getByText("On the chart now").nextElementSibling).toHaveTextContent(
      "trazodone 50 mg, at bedtime",
    )
    expect(within(stop).getByTestId("proposed-text")).toHaveTextContent("Stopped: nausea")
    expect(within(dialog).queryByRole("button", { name: "Edit" })).not.toBeInTheDocument()

    await user.click(within(start).getByRole("button", { name: "Accept" }))
    await user.click(within(stop).getByRole("button", { name: "Discard" }))

    expect(decideChartProposal).toHaveBeenCalledWith("note-1", "start-hydroxyzine", {
      decision: "accept",
    })
    expect(decideChartProposal).toHaveBeenCalledWith("note-1", "stop-trazodone", {
      decision: "discard",
    })
  })

  it("says the list was updated once accepted", async () => {
    vi.mocked(getChartProposals).mockResolvedValue(listing([{ ...STOP, decision: "accepted" }]))
    render(<ChartUpdatesPanel note={NOTE} />, { wrapper })

    expect(await screen.findByText("Medication list updated")).toBeInTheDocument()
    expect(screen.queryByText("Added to the chart")).not.toBeInTheDocument()
  })
})

describe("the highlighted change", () => {
  it("marks the words between what the chart and the proposal share", () => {
    expect(changeParts(SEPARATED, FINALIZED)).toEqual({
      before: SEPARATED,
      changed: " Divorce finalized April 2.",
      after: "",
    })
    expect(changeParts("Works at the library.", "Worked at the library until March.")).toEqual({
      before: "",
      changed: "Worked at the library until March.",
      after: "",
    })
    expect(changeParts(null, "Lives alone.").changed).toBe("Lives alone.")
  })
})
