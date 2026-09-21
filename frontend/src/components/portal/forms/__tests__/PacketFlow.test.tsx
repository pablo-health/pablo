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
  authoredItem,
} from "./formFixtures"

vi.mock("@/lib/api/patientIntake", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/patientIntake")>()
  return {
    ...actual,
    fetchAssignment: vi.fn(),
    saveAnswer: vi.fn(),
    submitAssignment: vi.fn(),
    // The consent renderer calls these itself; the walk never does.
    fetchConsentDocument: vi.fn(),
    listSignatures: vi.fn(),
    signConsentDocument: vi.fn(),
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
            key: "guardian",
            position: 0,
            item_type: "guardian",
            required: true,
            label: "Who is responsible for them?",
            help_text: null,
            config: {},
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

  it("does not save a question whose renderer writes for itself", async () => {
    // A card is counted and shown like any other question, and Continue
    // moves past it: its answer names the documents that arrived, and only
    // the route that records an arrival may write one.
    vi.mocked(api.fetchAssignment).mockResolvedValue(
      assignmentDetail({
        items: [
          {
            id: "55555555-5555-4555-8555-555555555555",
            key: "insurance",
            position: 0,
            item_type: "insurance_card",
            required: true,
            label: "A photo of your insurance card",
            help_text: null,
            config: { sides: "both" },
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

    expect(await screen.findByTestId("forms-upload-front")).toBeInTheDocument()
    // Counted, unlike the unaskable one above.
    expect(screen.getByTestId("forms-progress")).toHaveTextContent("Question 1 of 2")

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
    // Nothing was left out of this one, so the receipt has nothing to add.
    expect(screen.queryByTestId("forms-receipt-notes")).not.toBeInTheDocument()
  })

  it("shows a note the server sent, as the server wrote it", async () => {
    const note = "One question stopped applying as you answered, so your answer to it wasn't sent."
    vi.mocked(api.submitAssignment).mockResolvedValue({ ...RECEIPT, notes: [note] })
    const user = userEvent.setup()
    renderFlow()
    await walkToReview(user)

    await user.click(screen.getByTestId("forms-submit"))

    expect(await screen.findByTestId("forms-receipt-notes")).toHaveTextContent(note)
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

describe("a question whose renderer writes for itself", () => {
  /**
   * A form whose only question is a consent document, unsigned.
   *
   * The consent renderer fetches the document and the signatures itself, so
   * both are stubbed; what is under test is what the WALK does when that
   * renderer reports a write.
   */
  const CONSENT_ITEM_ID = "55555555-5555-4555-8555-555555555555"
  const DOCUMENT_ID = "66666666-6666-4666-8666-666666666666"
  const STATEMENT = "By typing my name I agree that this is my electronic signature."

  const consentItem = {
    id: CONSENT_ITEM_ID,
    key: "consent",
    position: 0,
    item_type: "consent_document",
    required: true,
    label: null,
    help_text: null,
    config: { document_key: "doc-key", document_version_id: DOCUMENT_ID },
    value: null,
  }

  const signatureRow: api.IntakeSignature = {
    id: "77777777-7777-4777-8777-777777777777",
    assignment_id: ASSIGNMENT_ID,
    item_id: CONSENT_ITEM_ID,
    document_version_id: DOCUMENT_ID,
    document_digest: "a".repeat(64),
    signer_role: "patient",
    signer_typed_name: "Ada Lovelace",
    consent_statement_version: "1",
    consent_statement: STATEMENT,
    signed_at: "2026-09-20T14:30:00+00:00",
    auth_strength: "stepped_up",
    session_id: "session-handle",
    evidence_digest: "b".repeat(64),
  }

  const consentOnly = () =>
    assignmentDetail({
      items: [consentItem],
      progress: { complete: false, missing: [CONSENT_ITEM_ID] },
    })

  beforeEach(() => {
    vi.mocked(api.fetchAssignment).mockResolvedValue(consentOnly())
    vi.mocked(api.fetchConsentDocument).mockResolvedValue({
      id: DOCUMENT_ID,
      document_key: "doc-key",
      title: "Consent to treatment",
      rendered_html: "<p>You are agreeing to be treated here.</p>",
      version: 1,
      digest: "a".repeat(64),
      requires_signature: true,
      signer_roles: ["patient"],
      consent_statement: STATEMENT,
      consent_statement_version: "1",
    })
    vi.mocked(api.listSignatures).mockResolvedValue([])
    vi.mocked(api.signConsentDocument).mockResolvedValue(signatureRow)
  })

  it("counts it as a question even though Continue does not save it", async () => {
    renderFlow()

    expect(await screen.findByTestId("forms-progress")).toHaveTextContent("Question 1 of 1")
  })

  it("never sends it through the save route", async () => {
    const user = userEvent.setup()
    renderFlow()
    await screen.findByTestId("forms-consent")

    await user.click(screen.getByTestId("forms-continue"))

    // Its answer names a signature row, and only the signing route may write
    // one — so Continue moves and nothing else.
    expect(api.saveAnswer).not.toHaveBeenCalled()
    expect(await screen.findByTestId("forms-review")).toBeInTheDocument()
  })

  it("stays on the question after a signature instead of jumping to review", async () => {
    // Where the walk sits is derived from `progress.missing` until somebody
    // navigates. Signing the last outstanding question empties that list, so
    // without pinning, the re-read would whisk the patient to the review
    // screen before the signature it had just taken was ever shown.
    const user = userEvent.setup()
    renderFlow()
    await screen.findByTestId("forms-consent")

    vi.mocked(api.fetchAssignment).mockResolvedValue(
      assignmentDetail({
        items: [{ ...consentItem, value: { signed: true, signature_id: signatureRow.id } }],
        progress: { complete: true, missing: [] },
      }),
    )
    vi.mocked(api.listSignatures).mockResolvedValue([signatureRow])

    await user.click(screen.getByTestId("forms-consent-affirm"))
    await user.type(screen.getByTestId("forms-consent-name"), "Ada Lovelace")
    await user.click(screen.getByTestId("forms-consent-sign"))

    expect(await screen.findByTestId("forms-consent-signed")).toHaveTextContent("Ada Lovelace")
    expect(screen.queryByTestId("forms-review")).not.toBeInTheDocument()
    expect(onChanged).toHaveBeenCalled()

    // And Continue is still what moves them on.
    await user.click(screen.getByTestId("forms-continue"))
    expect(await screen.findByTestId("forms-review")).toBeInTheDocument()
  })
})

describe("a question asked only of some people", () => {
  /** A yes-or-no, and a follow-up that only a yes opens. */
  function branchingItems(answer: Record<string, unknown> | null) {
    const trigger = authoredItem("yes_no", {
      id: "88888888-8888-4888-8888-888888888888",
      key: "substances",
      label: "Do you drink alcohol or use any other substances?",
    })
    const followUp = authoredItem("free_text", {
      id: "99999999-9999-4999-8999-999999999999",
      key: "which",
      label: "What, and roughly how often?",
      config: {
        max_len: 500,
        visible_when: { item_key: "substances", op: "eq", value: true },
      },
    })
    return [
      { ...trigger, position: 0, value: answer },
      { ...followUp, position: 1, value: null },
    ]
  }

  it("is not on the walk until the answer that opens it is given", async () => {
    vi.mocked(api.fetchAssignment).mockResolvedValue(
      assignmentDetail({
        items: branchingItems({ yes: false }),
        progress: { complete: true, missing: [] },
      }),
    )
    renderFlow()

    const review = await screen.findByTestId("forms-review")
    expect(within(review).getAllByTestId("forms-review-edit")).toHaveLength(1)
    expect(review).not.toHaveTextContent("What, and roughly how often?")
  })

  it("is on the walk once it is", async () => {
    vi.mocked(api.fetchAssignment).mockResolvedValue(
      assignmentDetail({
        items: branchingItems({ yes: true }),
        progress: { complete: false, missing: ["99999999-9999-4999-8999-999999999999"] },
      }),
    )
    renderFlow()

    expect(await screen.findByTestId("forms-item-screen")).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "What, and roughly how often?" })).toBeVisible()
  })

  it("appears the moment the answer that opens it is given", async () => {
    // Not after a refetch. The walk does not make one on Continue, so a
    // rule read off the server's copy alone would be a branch that never
    // fires inside one sitting.
    vi.mocked(api.fetchAssignment).mockResolvedValue(
      assignmentDetail({
        items: branchingItems(null),
        progress: { complete: false, missing: ["88888888-8888-4888-8888-888888888888"] },
      }),
    )
    const user = userEvent.setup()
    renderFlow()

    await screen.findByTestId("forms-item-screen")
    await user.click(within(screen.getByTestId("forms-yes-no")).getByText("Yes"))
    await user.click(screen.getByTestId("forms-continue"))

    expect(
      await screen.findByRole("heading", { name: "What, and roughly how often?" }),
    ).toBeVisible()
  })

  it("goes when the answer is taken back", async () => {
    vi.mocked(api.fetchAssignment).mockResolvedValue(
      assignmentDetail({
        items: branchingItems({ yes: true }),
        progress: { complete: false, missing: ["99999999-9999-4999-8999-999999999999"] },
      }),
    )
    const user = userEvent.setup()
    renderFlow()

    await screen.findByRole("heading", { name: "What, and roughly how often?" })
    await user.click(screen.getByTestId("forms-back"))
    await user.click(within(screen.getByTestId("forms-yes-no")).getByText("No"))
    await user.click(screen.getByTestId("forms-continue"))

    expect(await screen.findByTestId("forms-review")).toBeInTheDocument()
    expect(screen.getByTestId("forms-review")).not.toHaveTextContent(
      "What, and roughly how often?",
    )
  })

  it("starts at the beginning when the server names one this browser hid", async () => {
    // The two disagreeing about the rules is not something a patient should
    // meet as an error: the walk opens at the first question, and the
    // server is still what refuses an unfinished form.
    vi.mocked(api.fetchAssignment).mockResolvedValue(
      assignmentDetail({
        items: branchingItems({ yes: false }),
        progress: { complete: false, missing: ["99999999-9999-4999-8999-999999999999"] },
      }),
    )
    renderFlow()

    expect(await screen.findByTestId("forms-item-screen")).toBeInTheDocument()
    expect(
      screen.getByRole("heading", {
        name: "Do you drink alcohol or use any other substances?",
      }),
    ).toBeVisible()
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
