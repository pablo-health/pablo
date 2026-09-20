// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Walking one form: where it opens, what each Continue sends, and what the
 * four refusals do to the screen.
 *
 * The client is mocked so the assertions can be about the walk rather than
 * about HTTP, but `PatientIntakeError` stays the real class — the walk
 * branches on its kind, and a stand-in would let a wrong branch pass.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { UserEvent } from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import * as api from "@/lib/api/patientIntake"
import { PatientIntakeError } from "@/lib/api/patientIntake"
import { PacketFlow } from "../PacketFlow"
import { CRISIS_FOOTER } from "../formsCopy"
import {
  ASSIGNMENT_ID,
  GAD7_ITEM_COUNT,
  INTAKE_FORM,
  ITEM_IDS,
  PHQ9_ITEM_COUNT,
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
  }
})

const TOKEN = "portal-session-token"

const onSessionLost = vi.fn()
const onChanged = vi.fn()
const onClose = vi.fn()

function saved(overrides: Partial<api.SavedAnswer> = {}): api.SavedAnswer {
  return {
    item_id: ITEM_IDS.reason,
    saved_at: "2026-09-20T09:00:00Z",
    status: "in_progress",
    progress: { complete: false, missing: [] },
    ...overrides,
  }
}

function renderFlow() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "PacketFlowWrapper"
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

async function answerMeasure(user: UserEvent, code: string, count: number, label: string) {
  for (let index = 1; index <= count; index += 1) {
    const group = screen.getByTestId(`forms-item-${code}-${index}`)
    await user.click(within(group).getByRole("radio", { name: label }))
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.fetchAssignment).mockResolvedValue(assignmentDetail())
  vi.mocked(api.saveAnswer).mockResolvedValue(saved())
  vi.mocked(api.submitAssignment).mockResolvedValue(RECEIPT)
})

describe("where a form opens", () => {
  it("starts at the first question the server called outstanding", async () => {
    renderFlow()

    expect(await screen.findByTestId("forms-item-screen")).toBeInTheDocument()
    expect(screen.getByTestId("forms-identity-name")).toHaveTextContent("Dana Okonkwo")
    expect(screen.getByTestId("forms-progress")).toHaveTextContent("Question 1 of 4")
  })

  it("resumes at the first gap rather than at the start", async () => {
    // Two answered, then a gap: the walk lands on the measure, not on the
    // demographics question the patient settled on another device.
    vi.mocked(api.fetchAssignment).mockResolvedValue(
      assignmentDetail({
        status: "in_progress",
        items: [
          { ...SEEDED_ITEMS[0], value: { name_confirmed: true, dob_confirmed: true } },
          { ...SEEDED_ITEMS[1], value: { text: "Panic before every shift." } },
          SEEDED_ITEMS[2],
          SEEDED_ITEMS[3],
        ],
        progress: { complete: false, missing: [ITEM_IDS.phq9, ITEM_IDS.gad7] },
      }),
    )

    renderFlow()

    expect(await screen.findByTestId("forms-progress")).toHaveTextContent("Question 3 of 4")
    expect(screen.getByRole("heading", { name: "PHQ-9" })).toBeInTheDocument()
  })

  it("opens on the review screen when nothing is outstanding", async () => {
    vi.mocked(api.fetchAssignment).mockResolvedValue(
      assignmentDetail({
        status: "in_progress",
        progress: { complete: true, missing: [] },
      }),
    )

    renderFlow()

    expect(await screen.findByTestId("forms-review")).toBeInTheDocument()
  })
})

describe("saving", () => {
  it("sends exactly one answer per Continue, and nothing on Back", async () => {
    const user = userEvent.setup()
    renderFlow()
    await screen.findByTestId("forms-item-screen")

    await user.click(screen.getByTestId("forms-identity-confirm"))
    await user.click(screen.getByTestId("forms-continue"))

    await waitFor(() => expect(api.saveAnswer).toHaveBeenCalledTimes(1))
    expect(api.saveAnswer).toHaveBeenCalledWith(TOKEN, ASSIGNMENT_ID, ITEM_IDS.demographics, {
      name_confirmed: true,
      dob_confirmed: true,
      corrections: null,
    })

    await user.click(screen.getByTestId("forms-back"))
    expect(api.saveAnswer).toHaveBeenCalledTimes(1)
  })

  it("keeps what was typed when the server refuses the answer", async () => {
    const user = userEvent.setup()
    vi.mocked(api.saveAnswer).mockRejectedValue(
      new PatientIntakeError("invalid", "Refused", {
        serverMessage: "What brings you in is still blank.",
      }),
    )
    vi.mocked(api.fetchAssignment).mockResolvedValue(
      assignmentDetail({ progress: { complete: false, missing: [ITEM_IDS.reason] } }),
    )
    renderFlow()
    await screen.findByTestId("forms-reason")

    await user.click(screen.getByTestId("forms-continue"))

    expect(await screen.findByTestId("forms-item-error")).toHaveTextContent(
      "What brings you in is still blank.",
    )
    expect(screen.getByTestId("forms-reason")).toBeInTheDocument()
  })

  it("walks past a question it cannot ask without sending anything", async () => {
    vi.mocked(api.fetchAssignment).mockResolvedValue(
      assignmentDetail({
        items: [
          {
            id: "55555555-5555-4555-8555-555555555555",
            key: "insurance",
            position: 0,
            item_type: "insurance_card",
            required: true,
            config: { label: "Insurance card", sides: "both" },
            value: null,
          },
          SEEDED_ITEMS[1],
        ],
        progress: {
          complete: false,
          missing: ["55555555-5555-4555-8555-555555555555", ITEM_IDS.reason],
        },
      }),
    )
    const user = userEvent.setup()
    renderFlow()

    expect(await screen.findByTestId("forms-item-unavailable")).toHaveTextContent(
      "This step will be available soon.",
    )
    // Not counted among the questions, because it cannot be answered here.
    expect(screen.queryByTestId("forms-progress")).not.toBeInTheDocument()

    await user.click(screen.getByTestId("forms-continue"))

    expect(await screen.findByTestId("forms-reason")).toBeInTheDocument()
    expect(api.saveAnswer).not.toHaveBeenCalled()
  })

  it("hands a dead session back to the shell", async () => {
    const user = userEvent.setup()
    vi.mocked(api.saveAnswer).mockRejectedValue(
      new PatientIntakeError("expired", "Session expired"),
    )
    renderFlow()
    await screen.findByTestId("forms-item-screen")

    await user.click(screen.getByTestId("forms-identity-confirm"))
    await user.click(screen.getByTestId("forms-continue"))

    await waitFor(() => expect(onSessionLost).toHaveBeenCalled())
  })
})

describe("review and sending", () => {
  async function walkToReview(user: UserEvent) {
    await screen.findByTestId("forms-item-screen")
    await user.click(screen.getByTestId("forms-identity-confirm"))
    await user.click(screen.getByTestId("forms-continue"))

    await screen.findByTestId("forms-reason")
    await user.type(screen.getByTestId("forms-reason"), "Panic before every shift.")
    await user.click(screen.getByTestId("forms-continue"))

    await screen.findByRole("heading", { name: "PHQ-9" })
    await answerMeasure(user, "phq9", PHQ9_ITEM_COUNT, "Several days")
    await user.click(screen.getByTestId("forms-continue"))

    await screen.findByRole("heading", { name: "GAD-7" })
    await answerMeasure(user, "gad7", GAD7_ITEM_COUNT, "Not at all")
    await user.click(screen.getByTestId("forms-continue"))

    return screen.findByTestId("forms-review")
  }

  it("shows every question that collects an answer, and no score", async () => {
    const user = userEvent.setup()
    renderFlow()

    const review = await walkToReview(user)

    expect(within(review).getByText("Panic before every shift.")).toBeInTheDocument()
    expect(within(review).getAllByTestId("forms-review-edit")).toHaveLength(4)
    // What was said, never what it scores.
    expect(review.textContent).toMatch(/9 of 9 answered/)
    expect(review.textContent).not.toMatch(/moderate|severe|minimal|total/i)
  })

  it("goes back into a question from the review screen", async () => {
    const user = userEvent.setup()
    renderFlow()
    const review = await walkToReview(user)

    await user.click(within(review).getAllByTestId("forms-review-edit")[1])

    expect(await screen.findByTestId("forms-reason")).toHaveValue("Panic before every shift.")
  })

  it("sends once however many times the button is pressed", async () => {
    const user = userEvent.setup()
    vi.mocked(api.submitAssignment).mockImplementation(() => new Promise(() => {}))
    renderFlow()
    await walkToReview(user)

    const submit = screen.getByTestId("forms-submit")
    await user.click(submit)
    await user.click(submit)

    expect(api.submitAssignment).toHaveBeenCalledTimes(1)
  })

  it("shows the receipt code and the crisis line once it has gone", async () => {
    const user = userEvent.setup()
    renderFlow()
    await walkToReview(user)

    await user.click(screen.getByTestId("forms-submit"))

    expect(await screen.findByTestId("forms-receipt-code")).toHaveTextContent("K3MTQ7BX")
    expect(screen.getByTestId("forms-crisis-footer")).toHaveTextContent(CRISIS_FOOTER)
    // The 200 carried both totals and both bands. Neither reaches the screen.
    expect(screen.getByTestId("forms-receipt").textContent).not.toMatch(/12|moderate|mild/)
  })

  it("says what is outstanding when the server refuses an unfinished form", async () => {
    const user = userEvent.setup()
    vi.mocked(api.submitAssignment).mockRejectedValue(
      new PatientIntakeError("invalid", "Refused", {
        serverMessage: "Some questions still need an answer.",
        missing: [ITEM_IDS.gad7],
      }),
    )
    renderFlow()
    await walkToReview(user)

    await user.click(screen.getByTestId("forms-submit"))

    expect(await screen.findByTestId("forms-submit-error")).toHaveTextContent(
      "Some questions still need an answer.",
    )
  })

  it("says the form has gone when it was handed in from somewhere else", async () => {
    const user = userEvent.setup()
    vi.mocked(api.submitAssignment).mockRejectedValue(
      new PatientIntakeError("closed", "Form closed"),
    )
    renderFlow()
    await walkToReview(user)

    await user.click(screen.getByTestId("forms-submit"))

    expect(await screen.findByTestId("forms-already-sent")).toBeInTheDocument()
  })
})

describe("the crisis line", () => {
  it("rides every measure, whatever was answered", async () => {
    const user = userEvent.setup()
    renderFlow()
    await screen.findByTestId("forms-item-screen")

    expect(screen.queryByTestId("forms-crisis-footer")).not.toBeInTheDocument()

    await user.click(screen.getByTestId("forms-identity-confirm"))
    await user.click(screen.getByTestId("forms-continue"))
    await screen.findByTestId("forms-reason")
    await user.type(screen.getByTestId("forms-reason"), "Sleep.")
    await user.click(screen.getByTestId("forms-continue"))

    await screen.findByRole("heading", { name: "PHQ-9" })
    expect(screen.getByTestId("forms-crisis-footer")).toHaveTextContent(CRISIS_FOOTER)
  })
})
