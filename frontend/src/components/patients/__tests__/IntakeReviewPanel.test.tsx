// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * IntakeReviewPanel tests.
 *
 * The API module is mocked rather than the hook, so the paths, the query key
 * and the `enabled` guard are all exercised on the way through. What the
 * panel must get right: say where every answer came from, offer an action
 * only when the form is in a state that takes it, never offer to sign a
 * document on somebody's behalf, and render no section for a list that is
 * empty.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { IntakeReviewPanel } from "../IntakeReviewPanel"
import { renderWithProviders } from "@/test/renderWithProviders"
import type {
  IntakeAssignment,
  IntakeAssignmentStatus,
  IntakeReview,
  IntakeReviewItem,
  IntakeReviewSignature,
} from "@/lib/api/intakeReview"

const mockGet = vi.fn()
const mockRequest = vi.fn()
const mockAccept = vi.fn()
const mockEnter = vi.fn()
const mockExport = vi.fn()

vi.mock("@/lib/api/intakeReview", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/intakeReview")>()
  return {
    ...actual,
    getIntakeReview: (...args: unknown[]) => mockGet(...args),
    requestIntakeCorrection: (...args: unknown[]) => mockRequest(...args),
    acceptIntakeAssignment: (...args: unknown[]) => mockAccept(...args),
    enterIntakeAnswerForPatient: (...args: unknown[]) => mockEnter(...args),
    downloadIntakeExport: (...args: unknown[]) => mockExport(...args),
  }
})

/**
 * What the browser was asked to save, captured at the anchor.
 *
 * jsdom has no download, so the click is watched rather than followed: the
 * filename is the part the panel decides, and it is the part worth pinning.
 */
const saved: string[] = []

const SIGNED_AT = "2026-03-14T12:00:00Z"

function item(overrides: Partial<IntakeReviewItem> = {}): IntakeReviewItem {
  return {
    id: "item-1",
    key: "reason",
    position: 1,
    item_type: "text",
    required: true,
    label: "What brings you in?",
    help_text: null,
    config: {},
    value: { text: "Panic at work." },
    provenance: "patient",
    superseded_count: 0,
    ...overrides,
  }
}

function signature(
  overrides: Partial<IntakeReviewSignature> = {},
): IntakeReviewSignature {
  return {
    id: "sig-1",
    assignment_id: "assign-1",
    item_id: "item-3",
    document_version_id: "doc-1",
    document_digest: "digest",
    signer_role: "patient",
    signer_typed_name: "Ada Lovelace",
    consent_statement_version: "1",
    consent_statement: "I agree.",
    signed_at: SIGNED_AT,
    auth_strength: "session",
    session_id: null,
    evidence_digest: "evidence",
    ...overrides,
  }
}

function review(overrides: Partial<IntakeReview> = {}): IntakeReview {
  return {
    id: "assign-1",
    version_id: "version-1",
    packet_name: "New patient intake",
    version: 2,
    status: "submitted",
    assigned_at: "2026-03-01T12:00:00Z",
    submitted_at: SIGNED_AT,
    receipt_code: "ABC123",
    progress: { complete: true, missing: [] },
    patient_id: "patient-a",
    items: [item()],
    signatures: [],
    events: [],
    ...overrides,
  }
}

function assignment(): IntakeAssignment {
  const { id, version_id, packet_name, version, status, assigned_at, submitted_at, receipt_code, progress } =
    review()
  return { id, version_id, packet_name, version, status, assigned_at, submitted_at, receipt_code, progress }
}

function renderPanel() {
  return renderWithProviders(
    <IntakeReviewPanel patientId="patient-a" assignmentId="assign-1" />,
  )
}

describe("IntakeReviewPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockRequest.mockResolvedValue(assignment())
    mockAccept.mockResolvedValue(assignment())
    mockEnter.mockResolvedValue(assignment())
    saved.length = 0
    URL.createObjectURL = vi.fn(() => "blob:intake")
    URL.revokeObjectURL = vi.fn()
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      saved.push(this.download)
    })
  })

  it("says where each answer came from, and says nothing for an unanswered one", async () => {
    mockGet.mockResolvedValue(
      review({
        items: [
          item(),
          item({
            id: "item-2",
            key: "allergies",
            position: 2,
            label: "Allergies",
            value: null,
            provenance: null,
          }),
          item({
            id: "item-3",
            key: "pharmacy",
            position: 3,
            label: "Pharmacy",
            value: { text: "Corner Drug" },
            provenance: "clinician",
          }),
        ],
      }),
    )

    renderPanel()

    expect(await screen.findByTestId("intake-review-provenance-item-1")).toHaveTextContent(
      "Patient",
    )
    expect(screen.getByTestId("intake-review-provenance-item-3")).toHaveTextContent(
      "Entered by practice",
    )
    expect(screen.queryByTestId("intake-review-provenance-item-2")).not.toBeInTheDocument()
    expect(screen.getByTestId("intake-review-value-item-2")).toHaveTextContent("No answer")
  })

  it("offers the earlier-answers toggle only when there were earlier answers", async () => {
    mockGet.mockResolvedValue(
      review({
        items: [
          item(),
          item({ id: "item-2", key: "pharmacy", position: 2, superseded_count: 2 }),
        ],
      }),
    )

    renderPanel()

    await screen.findByTestId("intake-review-panel")
    expect(screen.queryByTestId("intake-review-earlier-toggle-item-1")).not.toBeInTheDocument()

    const toggle = screen.getByTestId("intake-review-earlier-toggle-item-2")
    expect(toggle).toHaveTextContent("2 earlier answers")
    expect(screen.queryByTestId("intake-review-earlier-detail-item-2")).not.toBeInTheDocument()

    await userEvent.click(toggle)
    expect(screen.getByTestId("intake-review-earlier-detail-item-2")).toHaveTextContent(
      "2 earlier answers were replaced.",
    )
  })

  it("sends the checked questions and the typed note", async () => {
    mockGet.mockResolvedValue(
      review({
        items: [item(), item({ id: "item-2", key: "pharmacy", position: 2, label: "Pharmacy" })],
      }),
    )

    renderPanel()

    const send = await screen.findByTestId("intake-review-request")
    expect(send).toBeDisabled()

    await userEvent.click(screen.getByTestId("intake-review-select-item-2"))
    expect(send).toBeDisabled()

    await userEvent.type(screen.getByTestId("intake-review-note"), "Add the pharmacy.")
    expect(send).toBeEnabled()

    await userEvent.click(send)

    expect(mockRequest).toHaveBeenCalledWith(
      "patient-a",
      "assign-1",
      { item_ids: ["item-2"], note: "Add the pharmacy." },
      undefined,
    )
  })

  it("keeps the note-only and selection-only states out of reach", async () => {
    mockGet.mockResolvedValue(review())

    renderPanel()

    const send = await screen.findByTestId("intake-review-request")
    await userEvent.type(screen.getByTestId("intake-review-note"), "Please redo this.")
    expect(send).toBeDisabled()

    await userEvent.click(screen.getByTestId("intake-review-select-item-1"))
    expect(send).toBeEnabled()
  })

  it.each<IntakeAssignmentStatus>(["assigned", "in_progress", "needs_correction", "accepted", "withdrawn"])(
    "offers neither accept nor corrections while the status is %s",
    async (status) => {
      mockGet.mockResolvedValue(review({ status }))

      renderPanel()

      await screen.findByTestId("intake-review-panel")
      expect(screen.queryByTestId("intake-review-accept")).not.toBeInTheDocument()
      expect(screen.queryByTestId("intake-review-request")).not.toBeInTheDocument()
      expect(screen.queryByTestId("intake-review-corrections")).not.toBeInTheDocument()
    },
  )

  it("accepts the form", async () => {
    mockGet.mockResolvedValue(review())

    renderPanel()

    await userEvent.click(await screen.findByTestId("intake-review-accept"))

    expect(mockAccept).toHaveBeenCalledWith("patient-a", "assign-1", undefined)
  })

  it("does not offer to enter an answer on a document somebody has to sign", async () => {
    mockGet.mockResolvedValue(
      review({
        items: [
          item(),
          item({
            id: "item-2",
            key: "consent",
            position: 2,
            item_type: "consent_document",
            label: "Consent to treat",
            value: null,
            provenance: null,
          }),
        ],
      }),
    )

    renderPanel()

    expect(await screen.findByTestId("intake-review-enter-item-1")).toBeInTheDocument()
    expect(screen.queryByTestId("intake-review-enter-item-2")).not.toBeInTheDocument()
  })

  it("writes an answer down for somebody in the room", async () => {
    mockGet.mockResolvedValue(review())

    renderPanel()

    await userEvent.click(await screen.findByTestId("intake-review-enter-item-1"))
    await userEvent.type(screen.getByTestId("intake-review-entry-input-item-1"), "Corner Drug")
    await userEvent.click(screen.getByTestId("intake-review-entry-save-item-1"))

    expect(mockEnter).toHaveBeenCalledWith(
      "patient-a",
      "assign-1",
      "item-1",
      { text: "Corner Drug" },
      undefined,
    )
  })

  it("renders no signatures section when nothing has been signed", async () => {
    mockGet.mockResolvedValue(review({ signatures: [] }))

    renderPanel()

    await screen.findByTestId("intake-review-panel")
    expect(screen.queryByTestId("intake-review-signatures")).not.toBeInTheDocument()
  })

  it("names who signed, in what role, when there is a signature", async () => {
    mockGet.mockResolvedValue(review({ signatures: [signature()] }))

    renderPanel()

    expect(await screen.findByTestId("intake-review-signature-sig-1")).toHaveTextContent(
      "Ada Lovelace signed as patient",
    )
  })

  it("downloads the form as a file named after its receipt", async () => {
    mockGet.mockResolvedValue(review())
    mockExport.mockResolvedValue(new Blob(["<!DOCTYPE html>"], { type: "text/html" }))

    renderPanel()
    await userEvent.click(await screen.findByTestId("intake-review-export"))

    expect(mockExport).toHaveBeenCalledWith("patient-a", "assign-1")
    expect(saved).toEqual(["intake-ABC123.html"])
  })

  it("names the file after the request when no form has been handed in", async () => {
    mockGet.mockResolvedValue(review({ status: "assigned", receipt_code: null }))
    mockExport.mockResolvedValue(new Blob(["<!DOCTYPE html>"], { type: "text/html" }))

    renderPanel()
    await userEvent.click(await screen.findByTestId("intake-review-export"))

    expect(saved).toEqual(["intake-assign-1.html"])
  })

  it("says so when the file could not be fetched, and saves nothing", async () => {
    mockGet.mockResolvedValue(review())
    mockExport.mockRejectedValue(new Error("network"))

    renderPanel()
    await userEvent.click(await screen.findByTestId("intake-review-export"))

    expect(await screen.findByTestId("intake-review-error")).toHaveTextContent(
      "That didn't go through.",
    )
    expect(saved).toEqual([])
  })

  it("says a form is finished only from the progress the server sent", async () => {
    mockGet.mockResolvedValue(
      review({ progress: { complete: false, missing: ["pharmacy", "allergies"] } }),
    )

    renderPanel()

    expect(await screen.findByTestId("intake-review-progress")).toHaveTextContent(
      "2 questions have no answer.",
    )
  })
})
