// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A form with sections, walked as parts.
 *
 * What is under test is the shape the patient sees: a section is never a
 * screen of its own, its title rides above each question in its part, the
 * count is kept to the part, and the review screen groups answers the same
 * way. Resuming and a form sent back for corrections land in the right part.
 *
 * The client is mocked so the assertions can be about the walk rather than
 * about HTTP.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { UserEvent } from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import * as api from "@/lib/api/patientIntake"
import type { IntakeAssignmentItem } from "@/lib/api/patientIntake"
import { ItemScreen } from "../ItemScreen"
import { PacketFlow } from "../PacketFlow"
import { ReviewScreen } from "../ReviewScreen"
import { narrowParts, notesFor, partsOf, placeOf } from "../parts"
import { ASSIGNMENT_ID, INTAKE_FORM, ITEM_IDS, SEEDED_ITEMS, assignmentDetail } from "./formFixtures"

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

function row(
  id: string,
  key: string,
  position: number,
  itemType: string,
  config: Record<string, unknown> = {},
  label: string | null = null,
): IntakeAssignmentItem {
  return {
    id,
    key,
    position,
    item_type: itemType,
    required: itemType !== "section" && itemType !== "instructions",
    label,
    help_text: null,
    config,
    value: null,
  }
}

const IDS = {
  medical: "a0000000-0000-4000-8000-000000000001",
  conditions: "a0000000-0000-4000-8000-000000000002",
  note: "a0000000-0000-4000-8000-000000000003",
  medications: "a0000000-0000-4000-8000-000000000004",
  substance: "a0000000-0000-4000-8000-000000000005",
  alcohol: "a0000000-0000-4000-8000-000000000006",
  empty: "a0000000-0000-4000-8000-000000000007",
}

/**
 * The opening the engine always asks, then two sections and a third with
 * nothing under it. Positions are given out of array order on purpose: the
 * walk sorts by position before it reads a part boundary.
 */
const SECTIONED: IntakeAssignmentItem[] = [
  row(IDS.substance, "substance_part", 6, "section", { title: "Substance use" }),
  row(IDS.alcohol, "alcohol", 7, "free_text", {}, "How much do you drink in a week?"),
  SEEDED_ITEMS[0],
  SEEDED_ITEMS[1],
  row(IDS.medical, "medical_part", 2, "section", { title: "Medical history" }),
  row(IDS.conditions, "conditions", 3, "free_text", {}, "Any health conditions?"),
  row(IDS.note, "medical_note", 4, "instructions", {
    body_markdown: "The next question is about medicines.",
  }),
  row(IDS.medications, "medications", 5, "free_text", {}, "What medicines do you take?"),
  row(IDS.empty, "empty_part", 8, "section", { title: "Nothing here" }),
]

const ALL_MISSING = [
  ITEM_IDS.demographics,
  ITEM_IDS.reason,
  IDS.conditions,
  IDS.medications,
  IDS.alcohol,
]

function renderFlow() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "PacketFlowPartsWrapper"
  return render(
    <PacketFlow
      sessionToken={TOKEN}
      assignmentId={ASSIGNMENT_ID}
      form={INTAKE_FORM}
      onSessionLost={vi.fn()}
      onChanged={vi.fn()}
      onClose={vi.fn()}
    />,
    { wrapper: Wrapper },
  )
}

function header() {
  return {
    count: screen.queryByTestId("forms-part-count")?.textContent ?? null,
    title: screen.queryByTestId("forms-part-title")?.textContent ?? null,
    progress: screen.queryByTestId("forms-progress")?.textContent ?? null,
  }
}

async function typeAndContinue(user: UserEvent, testId: string, text: string) {
  await user.type(await screen.findByTestId(testId), text)
  await user.click(screen.getByTestId("forms-continue"))
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.fetchAssignment).mockResolvedValue(
    assignmentDetail({ items: SECTIONED, progress: { complete: false, missing: ALL_MISSING } }),
  )
  vi.mocked(api.saveAnswer).mockResolvedValue({
    item_id: ITEM_IDS.reason,
    saved_at: "2026-09-20T09:00:00Z",
    status: "in_progress",
    progress: { complete: false, missing: [] },
  })
})

describe("a form with sections", () => {
  it("walks each section as a named part with its own count", async () => {
    const user = userEvent.setup()
    renderFlow()

    await screen.findByTestId("forms-item-screen")
    expect(header()).toEqual({ count: "Part 1 of 3", title: "About you", progress: "1 of 2" })
    await user.click(screen.getByTestId("forms-identity-confirm"))
    await user.click(screen.getByTestId("forms-continue"))

    await screen.findByTestId("forms-reason")
    expect(header()).toEqual({ count: "Part 1 of 3", title: "About you", progress: "2 of 2" })
    await typeAndContinue(user, "forms-reason", "Trouble sleeping.")

    // Straight onto the first question: the section is not a screen.
    await screen.findByText("Any health conditions?")
    expect(screen.queryByTestId("forms-item-section")).not.toBeInTheDocument()
    expect(header()).toEqual({
      count: "Part 2 of 3",
      title: "Medical history",
      progress: "1 of 2",
    })
    expect(screen.queryByTestId("forms-item-instructions")).not.toBeInTheDocument()
    await typeAndContinue(user, "forms-free-text", "Asthma.")

    // A paragraph to read is not a screen: it sits above the question it
    // leads into.
    await screen.findByText("What medicines do you take?")
    expect(screen.getByTestId("forms-item-instructions")).toHaveTextContent(
      "The next question is about medicines.",
    )
    expect(header().progress).toBe("2 of 2")
    await typeAndContinue(user, "forms-free-text", "None.")

    await screen.findByText("How much do you drink in a week?")
    expect(header()).toEqual({ count: "Part 3 of 3", title: "Substance use", progress: "1 of 1" })
    await typeAndContinue(user, "forms-free-text", "Two glasses.")

    // The empty section at the end is dropped, so the walk goes to review.
    expect(await screen.findByTestId("forms-review")).toBeInTheDocument()
    expect(screen.queryByText("Nothing here")).not.toBeInTheDocument()
    expect(api.saveAnswer).toHaveBeenCalledTimes(5)
  })

  it("groups the review screen by part", async () => {
    vi.mocked(api.fetchAssignment).mockResolvedValue(
      assignmentDetail({ items: SECTIONED, progress: { complete: true, missing: [] } }),
    )
    renderFlow()

    await screen.findByTestId("forms-review")
    const groups = screen.getAllByTestId("forms-review-part")
    expect(
      groups.map((group) => within(group).getByTestId("forms-review-part-title").textContent),
    ).toEqual(["About you", "Medical history", "Substance use"])
    expect(groups.map((group) => within(group).getAllByTestId("forms-review-edit").length)).toEqual(
      [2, 2, 1],
    )
  })

  it("resumes in the part the server's first gap belongs to", async () => {
    vi.mocked(api.fetchAssignment).mockResolvedValue(
      assignmentDetail({
        status: "in_progress",
        items: SECTIONED,
        progress: { complete: false, missing: [IDS.alcohol] },
      }),
    )
    renderFlow()

    await screen.findByText("How much do you drink in a week?")
    expect(header()).toEqual({ count: "Part 3 of 3", title: "Substance use", progress: "1 of 1" })
  })

  it("shows only the asked question on a correction, under its part's name", async () => {
    vi.mocked(api.fetchAssignment).mockResolvedValue(
      assignmentDetail({
        status: "needs_correction",
        items: SECTIONED,
        progress: { complete: true, missing: [] },
        correction: {
          requested_at: "2026-09-20T11:00:00Z",
          note: "Which condition did you mean?",
          item_ids: [IDS.conditions],
          outstanding: [IDS.conditions],
        },
      }),
    )
    renderFlow()

    await screen.findByText("Any health conditions?")
    // One part on this walk, so no count of parts.
    expect(header()).toEqual({ count: null, title: "Medical history", progress: "1 of 1" })
  })

  it("gives an opening that is not the identity check no name", async () => {
    vi.mocked(api.fetchAssignment).mockResolvedValue(
      assignmentDetail({
        items: [
          row(IDS.conditions, "conditions", 0, "free_text", {}, "Any health conditions?"),
          row(IDS.substance, "substance_part", 1, "section", { title: "Substance use" }),
          row(IDS.alcohol, "alcohol", 2, "free_text", {}, "How much do you drink in a week?"),
        ],
        progress: { complete: false, missing: [IDS.conditions, IDS.alcohol] },
      }),
    )
    renderFlow()

    await screen.findByText("Any health conditions?")
    expect(header()).toEqual({ count: "Part 1 of 2", title: null, progress: "Question 1 of 1" })
  })

  it("keeps the plain question count on a form with no sections", async () => {
    vi.mocked(api.fetchAssignment).mockResolvedValue(assignmentDetail())
    renderFlow()

    await screen.findByTestId("forms-item-screen")
    expect(header()).toEqual({ count: null, title: null, progress: "Question 1 of 4" })
  })
})

describe("a follow-up a rule opens inside a part", () => {
  const FOLLOW = {
    part: "b0000000-0000-4000-8000-000000000001",
    drinks: "b0000000-0000-4000-8000-000000000002",
    howMuch: "b0000000-0000-4000-8000-000000000003",
    smoke: "b0000000-0000-4000-8000-000000000004",
    intro: "b0000000-0000-4000-8000-000000000005",
    other: "b0000000-0000-4000-8000-000000000006",
  }
  const ITEMS: IntakeAssignmentItem[] = [
    row(FOLLOW.intro, "intro", 0, "free_text", {}, "Anything to tell us first?"),
    row(FOLLOW.part, "substances_part", 1, "section", { title: "Substances" }),
    row(FOLLOW.drinks, "drinks", 2, "yes_no", {}, "Do you drink alcohol?"),
    row(
      FOLLOW.howMuch,
      "how_much",
      3,
      "free_text",
      { visible_when: { item_key: "drinks", op: "eq", value: true } },
      "How much in a week?",
    ),
    row(FOLLOW.smoke, "smoke", 4, "free_text", {}, "Do you smoke?"),
  ]

  it("keeps the part's total steady and numbers the follow-up with its question", async () => {
    vi.mocked(api.fetchAssignment).mockResolvedValue(
      assignmentDetail({
        items: ITEMS,
        progress: { complete: false, missing: [FOLLOW.drinks, FOLLOW.smoke] },
      }),
    )
    const user = userEvent.setup()
    renderFlow()

    await screen.findByText("Do you drink alcohol?")
    expect(header().progress).toBe("1 of 2")
    await user.click(screen.getByRole("radio", { name: "Yes" }))
    await user.click(screen.getByTestId("forms-continue"))

    await screen.findByText("How much in a week?")
    expect(header()).toEqual({ count: "Part 2 of 2", title: "Substances", progress: "1 of 2" })
    await typeAndContinue(user, "forms-free-text", "Two glasses.")

    await screen.findByText("Do you smoke?")
    expect(header().progress).toBe("2 of 2")
  })

  it("counts a question a rule in an earlier part opened like any other", () => {
    const items = [
      row(FOLLOW.drinks, "drinks", 0, "yes_no", {}, "Do you drink alcohol?"),
      row(FOLLOW.part, "substances_part", 1, "section", { title: "Substances" }),
      row(
        FOLLOW.howMuch,
        "how_much",
        2,
        "free_text",
        { visible_when: { item_key: "drinks", op: "eq", value: true } },
        "How much in a week?",
      ),
      row(FOLLOW.smoke, "smoke", 3, "free_text", {}, "Do you smoke?"),
    ]
    const parts = partsOf(items)
    expect(placeOf(parts, items[2])?.question).toEqual({ index: 1, total: 2 })
    expect(placeOf(parts, items[3])?.question).toEqual({ index: 2, total: 2 })
  })
})

describe("partsOf", () => {
  const sorted = [...SECTIONED].sort((a, b) => a.position - b.position)

  it("splits on sections, drops empty ones, and names an identity opening", () => {
    const parts = partsOf(sorted)
    expect(parts.map((part) => part.title)).toEqual([
      "About you",
      "Medical history",
      "Substance use",
    ])
    expect(parts.flatMap((part) => part.screens).some((item) => item.item_type === "section")).toBe(
      false,
    )
  })

  it("does not name an opening that is the whole form", () => {
    expect(partsOf(SEEDED_ITEMS).map((part) => part.title)).toEqual([null])
  })

  it("folds instructions into the next screen of their part", () => {
    const parts = partsOf(sorted)
    const medical = parts[1]
    expect(medical.screens.map((item) => item.id)).toEqual([IDS.conditions, IDS.medications])
    expect(medical.notes[IDS.medications].map((item) => item.id)).toEqual([IDS.note])
    const medications = sorted.find((item) => item.id === IDS.medications)!
    expect(notesFor(parts, medications).map((item) => item.id)).toEqual([IDS.note])
  })

  it("keeps instructions with nothing after them in their part as a screen", () => {
    const items = [
      row(IDS.conditions, "conditions", 0, "free_text", {}, "Any health conditions?"),
      row(IDS.note, "closing_note", 1, "instructions", { body_markdown: "Thanks." }),
      row(IDS.substance, "substance_part", 2, "section", { title: "Substance use" }),
      row(IDS.alcohol, "alcohol", 3, "free_text", {}, "How much do you drink in a week?"),
    ]
    const parts = partsOf(items)
    expect(parts[0].screens.map((item) => item.id)).toEqual([IDS.conditions, IDS.note])
    expect(parts[1].notes).toEqual({})
    // Still uncounted on its own screen.
    expect(placeOf(parts, items[1])?.question).toBeNull()
  })

  it("treats a section with no title as a part with no name", () => {
    const parts = partsOf([
      row(IDS.medical, "untitled", 0, "section", { title: "  " }),
      row(IDS.conditions, "conditions", 1, "free_text", {}, "Any health conditions?"),
    ])
    expect(parts).toHaveLength(1)
    expect(parts[0].title).toBeNull()
  })

  it("counts inside the narrowed part", () => {
    const parts = narrowParts(partsOf(sorted), (item) => item.id === IDS.medications)
    const medications = sorted.find((item) => item.id === IDS.medications)!
    expect(placeOf(parts, medications)).toEqual({
      title: "Medical history",
      part: 1,
      parts: 1,
      question: { index: 1, total: 1 },
    })
  })
})

describe("ItemScreen header", () => {
  function renderItem(place: Parameters<typeof ItemScreen>[0]["place"]) {
    render(
      <ItemScreen
        item={row(IDS.conditions, "conditions", 0, "free_text", {}, "Any health conditions?")}
        value={null}
        onChange={vi.fn()}
        form={INTAKE_FORM}
        assignmentId={ASSIGNMENT_ID}
        sessionToken={TOKEN}
        artifacts={[]}
        onWrote={vi.fn()}
        onSessionLost={vi.fn()}
        onBack={null}
        onContinue={vi.fn()}
        saving={false}
        error={null}
        place={place}
      />,
    )
  }

  it("names the part and counts within it", () => {
    renderItem({ title: "Consent to telehealth", part: 3, parts: 8, question: { index: 2, total: 3 } })
    expect(header()).toEqual({
      count: "Part 3 of 8",
      title: "Consent to telehealth",
      progress: "2 of 3",
    })
  })

  it("shows nothing when it has no place", () => {
    renderItem(null)
    expect(screen.queryByTestId("forms-part-header")).not.toBeInTheDocument()
  })
})

describe("ReviewScreen grouping", () => {
  it("puts a heading over named parts only", () => {
    const parts = partsOf([
      row(IDS.conditions, "conditions", 0, "free_text", {}, "Any health conditions?"),
      row(IDS.substance, "substance_part", 1, "section", { title: "Substance use" }),
      row(IDS.alcohol, "alcohol", 2, "free_text", {}, "How much do you drink in a week?"),
    ])
    render(
      <ReviewScreen
        parts={parts}
        values={{ [IDS.conditions]: { text: "Asthma." }, [IDS.alcohol]: null }}
        form={INTAKE_FORM}
        sessionToken={TOKEN}
        onEdit={vi.fn()}
        onBack={null}
        onSubmit={vi.fn()}
        submitting={false}
        error={null}
      />,
    )
    const groups = screen.getAllByTestId("forms-review-part")
    expect(groups).toHaveLength(2)
    expect(within(groups[0]).queryByTestId("forms-review-part-title")).not.toBeInTheDocument()
    expect(within(groups[0]).getByText("Asthma.")).toBeInTheDocument()
    expect(within(groups[1]).getByTestId("forms-review-part-title")).toHaveTextContent(
      "Substance use",
    )
  })
})
