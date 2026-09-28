// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The chart's read-only views, one question type at a time.
 *
 * What each must get right: show the question in the wording it was asked,
 * mark the answer that was recorded and no other, offer no control that
 * changes anything, and say "No answer" rather than drawing an empty box.
 * The first test is the one that keeps the set whole — a type the portal
 * can ask with no view here fails by name.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { RENDERED_ITEM_TYPES } from "@/components/portal/forms/renderers/registry"
import { INTAKE_FORM } from "@/components/portal/forms/__tests__/formFixtures"
import type { IntakeChartArtifact, IntakeReviewItem, IntakeReviewSignature } from "@/lib/api/intakeReview"
import { renderWithProviders } from "@/test/renderWithProviders"
import { VIEWED_ITEM_TYPES, viewFor } from "../registry"

const mockDocument = vi.fn()
const mockDownloadUrl = vi.fn()

vi.mock("@/lib/api/intakeDocuments", () => ({
  getIntakeDocument: (...args: unknown[]) => mockDocument(...args),
}))
vi.mock("@/lib/api/patientDocuments", () => ({
  getPatientDocumentDownloadUrl: (...args: unknown[]) => mockDownloadUrl(...args),
}))

function item(overrides: Partial<IntakeReviewItem>): IntakeReviewItem {
  return {
    id: "item-1",
    key: "q",
    position: 1,
    item_type: "free_text",
    required: true,
    label: "The question",
    help_text: null,
    config: {},
    value: null,
    provenance: null,
    superseded_count: 0,
    ...overrides,
  }
}

function artifact(overrides: Partial<IntakeChartArtifact>): IntakeChartArtifact {
  return {
    id: "art-1",
    item_id: "item-1",
    item_label: "The question",
    side: null,
    document_id: "doc-1",
    filename: "file.pdf",
    content_type: "application/pdf",
    size_bytes: 1024,
    scan_status: null,
    created_at: "2026-03-14T12:00:00Z",
    ...overrides,
  }
}

function signature(overrides: Partial<IntakeReviewSignature> = {}): IntakeReviewSignature {
  return {
    id: "sig-1",
    assignment_id: "assign-1",
    item_id: "item-1",
    document_version_id: "docv-1",
    document_digest: "digest",
    signer_role: "patient",
    signer_typed_name: "Dana Okonkwo",
    consent_statement_version: "1",
    consent_statement: "I have read this document and agree to it.",
    signed_at: "2026-03-14T12:00:00Z",
    auth_strength: "stepped_up",
    session_id: null,
    evidence_digest: "evidence",
    ...overrides,
  }
}

function draw(
  shown: IntakeReviewItem,
  extra: { signatures?: IntakeReviewSignature[]; artifacts?: IntakeChartArtifact[] } = {},
) {
  const View = viewFor(shown.item_type).Component
  return renderWithProviders(
    <View item={shown} form={INTAKE_FORM} signatures={extra.signatures ?? []} artifacts={extra.artifacts ?? []} />,
  )
}

function radio(name: string) {
  return screen.getByRole("radio", { name })
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("the view registry", () => {
  it("has a view for every question the portal can ask", () => {
    expect([...VIEWED_ITEM_TYPES].sort()).toEqual([...RENDERED_ITEM_TYPES].sort())
  })

  it("marks headings and paragraphs as having nothing to answer", () => {
    expect(viewFor("section").answerable).toBe(false)
    expect(viewFor("instructions").answerable).toBe(false)
    expect(viewFor("free_text").answerable).toBe(true)
  })
})

describe("demographics", () => {
  it("shows the name and date of birth, and the confirmation chosen", () => {
    draw(item({ item_type: "demographics", label: null,
      value: { name_confirmed: true, dob_confirmed: true, corrections: null } }))
    expect(screen.getByText("Is this you?")).toBeInTheDocument()
    expect(screen.getByText("Dana Okonkwo")).toBeInTheDocument()
    expect(screen.getByText("1988-04-02")).toBeInTheDocument()
    expect(radio("Yes, that's me")).toBeChecked()
    expect(radio("Something's not right")).not.toBeChecked()
  })

  it("shows what the patient said to correct", () => {
    draw(item({ item_type: "demographics", label: null,
      value: { name_confirmed: false, dob_confirmed: false, corrections: "Middle name is Ada." } }))
    expect(radio("Something's not right")).toBeChecked()
    expect(screen.getByText("Middle name is Ada.")).toBeInTheDocument()
  })
})

describe("reason", () => {
  it("asks the engine's prompt and shows what was written", () => {
    draw(item({ item_type: "reason", label: null, value: { text: "Panic before every shift." } }))
    expect(screen.getByText("What brings you in?")).toBeInTheDocument()
    expect(screen.getByText("Panic before every shift.")).toBeInTheDocument()
  })
})

describe("instrument", () => {
  it("marks the chosen answer on every item, in the server's wording", () => {
    const scores = { "1": 3, "2": 0, "3": 1, "4": 2, "5": 0, "6": 0, "7": 1, "8": 0, "9": 0 }
    draw(item({ item_type: "instrument", label: null, config: { code: "phq9" }, value: { item_scores: scores } }))
    expect(screen.getByText("PHQ-9")).toBeInTheDocument()
    const first = screen.getByRole("group", { name: "Little interest or pleasure in doing things" })
    expect(within(first).getByRole("radio", { name: "Nearly every day" })).toBeChecked()
    expect(within(first).getAllByRole("radio").filter((r) => (r as HTMLInputElement).checked)).toHaveLength(1)
    const ninth = screen.getByRole("group", {
      name: "Thoughts that you would be better off dead, or of hurting yourself in some way",
    })
    expect(within(ninth).getByRole("radio", { name: "Not at all" })).toBeChecked()
    for (const input of screen.getAllByRole("radio")) expect(input).toBeDisabled()
  })

  it("shows no total and no severity", () => {
    const scores = Object.fromEntries(Array.from({ length: 7 }, (_, i) => [String(i + 1), 3]))
    const { container } = draw(item({ item_type: "instrument", config: { code: "gad7" }, value: { item_scores: scores } }))
    expect(container).not.toHaveTextContent(/21|severe|total/i)
  })

  it("lists the scores by item when the wording did not arrive", () => {
    draw(item({ item_type: "instrument", label: null, key: "other", config: { code: "unknown" },
      value: { item_scores: { "1": 2 } } }))
    expect(screen.getByText("1: 2")).toBeInTheDocument()
  })

  it("says so when nothing was answered", () => {
    draw(item({ item_type: "instrument", config: { code: "gad7" }, value: null }))
    expect(screen.getByText("No answer")).toBeInTheDocument()
  })
})

describe("section and instructions", () => {
  it("shows a heading", () => {
    draw(item({ item_type: "section", label: null, config: { title: "About you" } }))
    expect(screen.getByRole("heading", { name: "About you" })).toBeInTheDocument()
    expect(screen.queryByText("No answer")).not.toBeInTheDocument()
  })

  it("shows a paragraph", () => {
    draw(item({ item_type: "instructions", label: null, config: { body_markdown: "Take your time." } }))
    expect(screen.getByText("Take your time.")).toBeInTheDocument()
    expect(screen.queryByText("No answer")).not.toBeInTheDocument()
  })
})

describe("free text", () => {
  it("shows what was written under the question", () => {
    draw(item({ item_type: "free_text", help_text: "A sentence is fine.", value: { text: "Sleep better." } }))
    expect(screen.getByText("The question")).toBeInTheDocument()
    expect(screen.getByText("A sentence is fine.")).toBeInTheDocument()
    expect(screen.getByText("Sleep better.")).toBeInTheDocument()
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument()
  })

  it("says so when nothing was written", () => {
    draw(item({ item_type: "free_text", value: null }))
    expect(screen.getByText("No answer")).toBeInTheDocument()
  })
})

const OPTIONS = { options: [{ key: "a", label: "Mornings" }, { key: "b", label: "Evenings" }, { key: "c", label: "Weekends" }] }

describe("single choice", () => {
  it("marks the one chosen", () => {
    draw(item({ item_type: "single_choice", config: OPTIONS, value: { key: "b" } }))
    expect(radio("Evenings")).toBeChecked()
    expect(radio("Mornings")).not.toBeChecked()
    expect(radio("Evenings")).toBeDisabled()
  })
})

describe("multi choice", () => {
  it("marks every one chosen", () => {
    draw(item({ item_type: "multi_choice", config: OPTIONS, value: { keys: ["a", "c"] } }))
    expect(screen.getByRole("checkbox", { name: "Mornings" })).toBeChecked()
    expect(screen.getByRole("checkbox", { name: "Evenings" })).not.toBeChecked()
    expect(screen.getByRole("checkbox", { name: "Weekends" })).toBeChecked()
  })
})

describe("yes or no", () => {
  it("marks the answer and shows the follow-up behind a yes", () => {
    draw(item({ item_type: "yes_no", config: { follow_up_label: "Tell us more" },
      value: { yes: true, follow_up: "Twice last year." } }))
    expect(radio("Yes")).toBeChecked()
    expect(radio("No")).not.toBeChecked()
    expect(screen.getByText("Tell us more")).toBeInTheDocument()
    expect(screen.getByText("Twice last year.")).toBeInTheDocument()
  })

  it("marks a no", () => {
    draw(item({ item_type: "yes_no", value: { yes: false } }))
    expect(radio("No")).toBeChecked()
  })
})

describe("scale", () => {
  it("marks the point chosen, between the anchors", () => {
    draw(item({ item_type: "scale", config: { min: 1, max: 5, min_label: "Not at all", max_label: "Very much" },
      value: { value: 4 } }))
    expect(radio("4")).toBeChecked()
    expect(radio("5")).not.toBeChecked()
    expect(screen.getByText("Not at all")).toBeInTheDocument()
    expect(screen.getByText("Very much")).toBeInTheDocument()
  })
})

describe("number", () => {
  it("shows the number with its unit", () => {
    draw(item({ item_type: "number", config: { unit: "hours" }, value: { value: 6 } }))
    expect(screen.getByText("6 hours")).toBeInTheDocument()
  })
})

describe("date", () => {
  it("shows the date in the reader's format, on the day that was entered", () => {
    draw(item({ item_type: "date", value: { value: "2026-01-05" } }))
    expect(screen.getByText(new Date(2026, 0, 5).toLocaleDateString())).toBeInTheDocument()
  })
})

describe("emergency contact", () => {
  it("shows all three fields under their labels", () => {
    draw(item({ item_type: "emergency_contact",
      value: { name: "Sam Rivera", relationship: "Sibling", phone: "555-0100" } }))
    expect(screen.getByText("Their name")).toBeInTheDocument()
    expect(screen.getByText("Sam Rivera")).toBeInTheDocument()
    expect(screen.getByText("Sibling")).toBeInTheDocument()
    expect(screen.getByText("555-0100")).toBeInTheDocument()
  })
})

describe("consent document", () => {
  it("shows each signature with the statement that was agreed", () => {
    draw(item({ item_type: "consent_document", label: "Consent to treatment",
      config: { document_key: "consent", document_version_id: "docv-1" }, value: { signed: true, signature_id: "sig-1" } }),
    { signatures: [signature()] })
    expect(screen.getByText("Consent to treatment")).toBeInTheDocument()
    expect(screen.getByRole("checkbox", { name: "I have read this document and agree to it." })).toBeChecked()
    expect(screen.getByTestId("intake-view-signature-sig-1")).toHaveTextContent("Dana Okonkwo")
    expect(screen.getByTestId("intake-view-signature-sig-1")).toHaveTextContent("signed as patient")
  })

  it("opens the pinned version of the document only when asked", async () => {
    mockDocument.mockResolvedValue({ rendered_html: "<p>We will keep your records private.</p>" })
    draw(item({ item_type: "consent_document", config: { document_version_id: "docv-1" } }), { signatures: [] })
    expect(mockDocument).not.toHaveBeenCalled()
    expect(screen.getByText("No answer")).toBeInTheDocument()

    await userEvent.click(screen.getByTestId("intake-view-consent-toggle-item-1"))
    expect(await screen.findByText("We will keep your records private.")).toBeInTheDocument()
    expect(mockDocument).toHaveBeenCalledWith("docv-1")
  })
})

describe("insurance card", () => {
  it("names each side, front first", () => {
    draw(item({ item_type: "insurance_card" }), {
      artifacts: [
        artifact({ id: "back", side: "back", filename: "back.jpg" }),
        artifact({ id: "front", side: "front", filename: "front.jpg" }),
      ],
    })
    const rows = screen.getAllByRole("listitem")
    expect(rows[0]).toHaveTextContent("Front of card")
    expect(rows[0]).toHaveTextContent("front.jpg")
    expect(rows[1]).toHaveTextContent("Back of card")
  })
})

describe("document request", () => {
  it("opens a file through the chart's document route", async () => {
    mockDownloadUrl.mockResolvedValue("https://files.example/signed")
    const open = vi.spyOn(window, "open").mockImplementation(() => null)
    draw(item({ item_type: "document_request" }), { artifacts: [artifact({ filename: "referral.pdf" })] })

    expect(screen.getByText("referral.pdf")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Open" }))
    expect(mockDownloadUrl).toHaveBeenCalledWith("doc-1", undefined, "inline")
    expect(open).toHaveBeenCalledWith("https://files.example/signed", "_blank", "noopener,noreferrer")
  })

  it("says so when nothing was sent", () => {
    draw(item({ item_type: "document_request" }))
    expect(screen.getByText("No files sent.")).toBeInTheDocument()
  })
})

describe("a type the chart has no view for", () => {
  it("spells the stored answer out under the question", () => {
    draw(item({ item_type: "guardian", label: "Guardian", value: { name: "Pat" } }))
    expect(screen.getByText("Guardian")).toBeInTheDocument()
    expect(screen.getByText("name: Pat")).toBeInTheDocument()
  })
})
