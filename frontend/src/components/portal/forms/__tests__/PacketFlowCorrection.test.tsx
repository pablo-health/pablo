// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A form the practice has sent back with a question about one answer.
 *
 * What is under test is the narrowing: only the questions the practice
 * named are on screen, the walk opens on the first one they are still
 * waiting for, and their note is shown as they wrote it.
 *
 * Which questions those are is the server's answer, carried on the
 * assignment. So every case here starts from a `correction` block the way
 * the route assembles one, and the walk is asked what it does with it.
 *
 * The client is mocked so the assertions can be about the walk rather than
 * about HTTP, and `PatientIntakeError` stays the real class — the walk
 * branches on its kind, and a stand-in would let a wrong branch pass.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import * as api from "@/lib/api/patientIntake"
import { PatientIntakeError } from "@/lib/api/patientIntake"
import { AssignmentList } from "../AssignmentList"
import { PacketFlow } from "../PacketFlow"
import { CORRECTION_SUBMIT, LIST_CORRECTION } from "../formsCopy"
import {
  ASSIGNMENT,
  ASSIGNMENT_ID,
  INTAKE_FORM,
  ITEM_IDS,
  RECEIPT,
  SEEDED_ITEMS,
  assignmentDetail,
} from "./formFixtures"

vi.mock("@/lib/api/patientIntake", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/patientIntake")>()
  return {
    ...actual,
    fetchAssignment: vi.fn(),
    saveAnswer: vi.fn(),
    submitAssignment: vi.fn(),
    fetchConsentDocument: vi.fn(),
    listSignatures: vi.fn(),
    signConsentDocument: vi.fn(),
  }
})

const TOKEN = "portal-session-token"
const NOTE = "The date you gave for when this started looks like a typo. Have another look?"

const onSessionLost = vi.fn()
const onChanged = vi.fn()
const onClose = vi.fn()

/** The whole form answered and handed in, with one question reopened. */
function sentBack(itemIds: string[], outstanding: string[] = itemIds): api.IntakeAssignmentDetail {
  return assignmentDetail({
    status: "needs_correction",
    submitted_at: "2026-09-20T09:30:00Z",
    receipt_code: "K3MTQ7BX",
    items: [
      { ...SEEDED_ITEMS[0], value: { name_confirmed: true, dob_confirmed: true } },
      { ...SEEDED_ITEMS[1], value: { text: "Panic before every shift." } },
      { ...SEEDED_ITEMS[2], value: { item_scores: {} } },
      { ...SEEDED_ITEMS[3], value: { item_scores: {} } },
    ],
    progress: { complete: true, missing: [] },
    correction: {
      requested_at: "2026-09-20T11:00:00Z",
      note: NOTE,
      item_ids: itemIds,
      outstanding,
    },
  })
}

function renderFlow() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "PacketFlowCorrectionWrapper"
  return render(
    <PacketFlow
      sessionToken={TOKEN}
      assignmentId={ASSIGNMENT_ID}
      form={INTAKE_FORM}
      onSessionLost={onSessionLost}
      onChanged={onChanged}
      onClose={onClose}
    />,
    { wrapper: Wrapper },
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.saveAnswer).mockResolvedValue({
    item_id: ITEM_IDS.reason,
    saved_at: "2026-09-20T11:05:00Z",
    status: "needs_correction",
    progress: { complete: true, missing: [] },
  })
  vi.mocked(api.submitAssignment).mockResolvedValue(RECEIPT)
})

describe("a form the practice sent back", () => {
  it("shows only the question they asked about", async () => {
    vi.mocked(api.fetchAssignment).mockResolvedValue(sentBack([ITEM_IDS.reason]))
    renderFlow()

    // The reopened question, and the one-of-one that says the rest are not
    // being asked again.
    expect(await screen.findByTestId("forms-reason")).toBeInTheDocument()
    expect(screen.getByTestId("forms-progress")).toHaveTextContent("Question 1 of 1")
    expect(screen.queryByTestId("forms-identity-name")).not.toBeInTheDocument()
  })

  it("opens on the first question they are still waiting for", async () => {
    // Both named; the reason has already been redone, so the walk lands on
    // the other one rather than on the first in the list.
    vi.mocked(api.fetchAssignment).mockResolvedValue(
      sentBack([ITEM_IDS.reason, ITEM_IDS.phq9], [ITEM_IDS.phq9]),
    )
    renderFlow()

    expect(await screen.findByTestId("forms-item-screen")).toBeInTheDocument()
    expect(screen.getByTestId("forms-progress")).toHaveTextContent("Question 2 of 2")
  })

  it("shows the note the practice wrote, as they wrote it", async () => {
    vi.mocked(api.fetchAssignment).mockResolvedValue(sentBack([ITEM_IDS.reason]))
    const user = userEvent.setup()
    renderFlow()

    await user.click(await screen.findByTestId("forms-continue"))
    expect(await screen.findByTestId("forms-correction-note")).toHaveTextContent(NOTE)
    expect(screen.getByTestId("forms-submit")).toHaveTextContent(CORRECTION_SUBMIT)
  })

  it("sends the redone answer and then the form", async () => {
    vi.mocked(api.fetchAssignment).mockResolvedValue(sentBack([ITEM_IDS.reason]))
    const user = userEvent.setup()
    renderFlow()

    const box = await screen.findByTestId("forms-reason")
    await user.clear(box)
    await user.type(box, "Panic before every shift, since about March.")
    await user.click(screen.getByTestId("forms-continue"))

    await waitFor(() => expect(api.saveAnswer).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.saveAnswer).mock.calls[0][2]).toBe(ITEM_IDS.reason)

    await user.click(await screen.findByTestId("forms-submit"))
    await waitFor(() => expect(api.submitAssignment).toHaveBeenCalledTimes(1))
  })

  it("reports the server's refusal when a correction is still outstanding", async () => {
    // The server's sentence, not one this screen invented: it is the thing
    // that knows which questions are still waiting.
    vi.mocked(api.fetchAssignment).mockResolvedValue(sentBack([ITEM_IDS.reason]))
    vi.mocked(api.submitAssignment).mockRejectedValue(
      new PatientIntakeError("invalid", "Refused", {
        serverMessage: "Your practice is still waiting on one of these.",
        missing: [ITEM_IDS.reason],
      }),
    )
    const user = userEvent.setup()
    renderFlow()

    await user.click(await screen.findByTestId("forms-continue"))
    await user.click(await screen.findByTestId("forms-submit"))

    expect(await screen.findByTestId("forms-submit-error")).toHaveTextContent(
      "Your practice is still waiting on one of these.",
    )
  })

  it("is an ordinary review screen when nothing was sent back", async () => {
    vi.mocked(api.fetchAssignment).mockResolvedValue(
      assignmentDetail({
        status: "in_progress",
        items: SEEDED_ITEMS.map((row) => ({ ...row, value: { text: "x" } })),
        progress: { complete: true, missing: [] },
      }),
    )
    renderFlow()

    expect(await screen.findByTestId("forms-review")).toBeInTheDocument()
    expect(screen.queryByTestId("forms-correction-note")).not.toBeInTheDocument()
  })
})

describe("the row on the list of forms", () => {
  it("says somebody is waiting rather than counting questions", () => {
    render(
      <AssignmentList
        assignments={[{ ...ASSIGNMENT, status: "needs_correction" }]}
        onOpen={vi.fn()}
      />,
    )
    expect(screen.getByTestId("forms-list-state")).toHaveTextContent(LIST_CORRECTION)
  })

  it("still offers a way back in", () => {
    render(
      <AssignmentList
        assignments={[{ ...ASSIGNMENT, status: "needs_correction" }]}
        onOpen={vi.fn()}
      />,
    )
    expect(screen.getByTestId("forms-list-open")).toBeInTheDocument()
  })
})
