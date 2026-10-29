// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The medical decision making panel beside a prescriber's note: the
 * clinician's three choices with the drafted evidence, the code they give,
 * the add-on from the confirmed minutes, and a dictated code that disagrees
 * flagged rather than changed. The note type is the follow-up as the server
 * serves it (`builtinNoteTypes.json`).
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MdmReviewPanel } from "../MdmReviewPanel"
import { createMockNote } from "@/test/factories"
import builtinNoteTypes from "@/test/fixtures/builtinNoteTypes.json"
import type { MdmReview } from "@/types/mdm"
import type { NoteTypeSchema } from "@/types/noteTypes"

const FOLLOW_UP = (builtinNoteTypes as unknown as NoteTypeSchema[]).find(
  (t) => t.key === "psychiatric_follow_up",
)!
const SOAP = (builtinNoteTypes as unknown as NoteTypeSchema[]).find((t) => t.key === "soap")!

const mockNoteType = vi.fn()
const mockReview = vi.fn()
const mockSave = vi.fn()
const mockApply = vi.fn()

vi.mock("@/hooks/useNoteTypes", () => ({ useNoteType: () => ({ data: mockNoteType() }) }))
vi.mock("@/hooks/useMdmReview", () => ({
  useMdmReview: () => ({ data: mockReview() }),
  useSaveMdmChoices: () => ({ mutate: mockSave, isError: false }),
  useApplyMdmCodes: () => ({ mutate: mockApply, isPending: false, isError: false }),
}))

function review(overrides: Partial<MdmReview> = {}): MdmReview {
  return {
    elements: [
      {
        element: "problems",
        chosen: "moderate",
        evidence: "Anxiety worse over two weeks; ADHD stable.",
        required: "moderate",
        meets: true,
      },
      {
        element: "risk",
        chosen: "moderate",
        evidence: "Prescription drug management: sertraline increased.",
        required: "moderate",
        meets: true,
      },
      { element: "data", chosen: "limited", evidence: "GAD-7 reviewed.", required: "moderate", meets: false },
    ],
    new_patient: false,
    level: "moderate",
    em_code: "99214",
    has_psychotherapy: false,
    psychotherapy_minutes: null,
    add_on: null,
    add_on_known: true,
    billing_methods: ["mdm", "time"],
    dictated_em_code: null,
    dictated_add_on: null,
    em_disagrees: false,
    add_on_disagrees: false,
    visit_details_with_codes: null,
    ...overrides,
  }
}

function renderPanel(editable = true) {
  const beforeApply = vi.fn(async () => {})
  render(
    <MdmReviewPanel
      note={createMockNote({ note_type: "psychiatric_follow_up" })}
      editable={editable}
      beforeApply={beforeApply}
    />,
  )
  return beforeApply
}

describe("MdmReviewPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockNoteType.mockReturnValue(FOLLOW_UP)
    mockReview.mockReturnValue(review())
  })

  it("shows each choice with its evidence, problems and risk first, and the code", () => {
    renderPanel()

    const elements = screen.getAllByTestId(/^mdm-element-/).map((e) => e.dataset.testid)
    expect(elements).toEqual(["mdm-element-problems", "mdm-element-risk", "mdm-element-data"])
    expect(screen.getByLabelText("Problems addressed")).toHaveValue("moderate")
    expect(screen.getByLabelText("Data reviewed")).toHaveValue("limited")
    expect(
      within(screen.getByTestId("mdm-element-risk")).getByText(
        "Prescription drug management: sertraline increased.",
      ),
    ).toBeInTheDocument()
    expect(within(screen.getByTestId("mdm-element-data")).getByTestId("mdm-meets")).toHaveTextContent(
      "Below moderate; the other two set the level",
    )
    expect(screen.getByTestId("mdm-level")).toHaveTextContent("Moderate")
    expect(screen.getByTestId("mdm-em-code")).toHaveTextContent(
      "99214 · Medical visit, established — moderate complexity",
    )
  })

  it("offers the type's levels and saves every choice when one changes", async () => {
    renderPanel()

    const options = within(screen.getByLabelText("Risk of management"))
      .getAllByRole("option")
      .map((o) => o.textContent)
    expect(options).toEqual(["Choose…", "Minimal", "Low", "Moderate", "High"])

    await userEvent.selectOptions(screen.getByLabelText("Data reviewed"), "moderate")

    expect(mockSave).toHaveBeenCalledWith(
      { problems: "moderate", data: "moderate", risk: "moderate", new_patient: "established" },
      expect.anything(),
    )
  })

  it("offers time as a way to choose the level only without psychotherapy", () => {
    renderPanel()
    expect(screen.getByTestId("mdm-billing-time")).toBeInTheDocument()
  })

  it("does not offer time once the note has psychotherapy, and shows the add-on", () => {
    mockReview.mockReturnValue(
      review({
        has_psychotherapy: true,
        psychotherapy_minutes: 20,
        add_on: "90833",
        billing_methods: ["mdm"],
      }),
    )
    renderPanel()

    expect(screen.queryByTestId("mdm-billing-time")).not.toBeInTheDocument()
    expect(screen.getByTestId("mdm-billing-methods")).toHaveTextContent("Medical decision making")
    expect(screen.getByTestId("mdm-add-on")).toHaveTextContent("90833 · 20 confirmed minutes")
  })

  it("waits for confirmed minutes before naming an add-on", () => {
    mockReview.mockReturnValue(
      review({ has_psychotherapy: true, add_on_known: false, billing_methods: ["mdm"] }),
    )
    renderPanel()

    expect(screen.getByTestId("mdm-add-on")).toHaveTextContent(
      "Confirm the psychotherapy minutes to see it",
    )
  })

  it("flags a dictated add-on the minutes do not support and offers the computed codes", async () => {
    const applied = "E/M code: 99214. Psychotherapy add-on code: none."
    mockReview.mockReturnValue(
      review({
        has_psychotherapy: true,
        psychotherapy_minutes: 12,
        billing_methods: ["mdm"],
        dictated_em_code: "99214",
        dictated_add_on: "90833",
        add_on_disagrees: true,
        visit_details_with_codes: applied,
      }),
    )
    const beforeApply = renderPanel()

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Dictated 90833; 12 confirmed minutes supports no add-on.",
    )
    // Flagged, never changed by itself.
    expect(mockApply).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole("button", { name: "Use the computed codes" }))
    // The page settles its own edits first, so the codes land on the note as it stands.
    expect(beforeApply).toHaveBeenCalled()
    expect(mockApply).toHaveBeenCalledTimes(1)
  })

  it("flags a dictated E/M code the choices do not support", () => {
    mockReview.mockReturnValue(
      review({
        dictated_em_code: "99213",
        em_disagrees: true,
        visit_details_with_codes: "Billing 99214.",
      }),
    )
    renderPanel()

    expect(screen.getByRole("alert")).toHaveTextContent("Dictated 99213; your choices support 99214.")
  })

  it("offers to add codes the note does not state yet", () => {
    mockReview.mockReturnValue(review({ visit_details_with_codes: "Date of service.\nE/M code: 99214" }))
    renderPanel()

    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Add 99214 to the note" })).toBeInTheDocument()
  })

  it("is read-only on a signed note", () => {
    mockReview.mockReturnValue(review({ visit_details_with_codes: "Billing 99214." }))
    renderPanel(false)

    expect(screen.getByLabelText("Problems addressed")).toBeDisabled()
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })

  it("asks for the levels before naming a code", () => {
    mockReview.mockReturnValue(
      review({
        elements: review().elements.map((e) => ({ ...e, chosen: null, required: null, meets: null })),
        level: null,
        em_code: null,
      }),
    )
    renderPanel()

    expect(screen.getByTestId("mdm-level")).toHaveTextContent("Choose all three to see the level")
    expect(screen.queryByTestId("mdm-meets")).not.toBeInTheDocument()
  })

  it("is not shown for a type without medical decision making", () => {
    mockNoteType.mockReturnValue(SOAP)
    renderPanel()

    expect(screen.queryByTestId("mdm-review")).not.toBeInTheDocument()
  })
})
