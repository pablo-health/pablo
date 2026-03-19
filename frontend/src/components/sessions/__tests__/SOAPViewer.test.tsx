// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SOAPViewer Component Tests
 *
 * Comprehensive tests for the document view, structured sub-field editing,
 * clinician observation form, save/cancel flows, PDF export, and status modes.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { SOAPViewer } from "../SOAPViewer"
import type { SOAPNoteModel } from "@/types/sessions"
import {
  createMockSession,
  createMockSOAPNote,
  createMockStructuredSOAPNote,
} from "@/test/factories"
import {
  SECTION_SUBFIELDS,
  narrativeToStructured,
  structuredToNarrative,
} from "../SubFieldEditor"

vi.mock("@/lib/utils/pdfExport", () => ({
  exportSOAPToPDF: vi.fn(),
}))

vi.mock("@/lib/config", () => ({
  useConfig: () => ({ showVerificationBadges: true }),
}))

// Narrative with **Label:** sub-field format (matches backend to_narrative output)
const structuredNarrative: SOAPNoteModel = createMockSOAPNote({
  subjective:
    "**Chief Complaint:** Anxiety about work\n\n**Mood/Affect:** Anxious but hopeful\n\n**Symptoms:**\n- Insomnia\n- Racing thoughts\n\n**Client Narrative:** Describes stress at work",
  objective:
    "**Appearance:** Well-groomed\n\n**Behavior:** Cooperative\n\n**Speech:** Normal rate\n\n**Thought Process:** Linear\n\n**Affect Observed:** Congruent",
  assessment:
    "**Clinical Impression:** Improving\n\n**Progress:** Moderate\n\n**Risk Assessment:** No concerns\n\n**Functioning Level:** Good",
  plan:
    "**Interventions Used:**\n- CBT restructuring\n\n**Homework Assignments:**\n- Daily mindfulness\n\n**Next Steps:**\n- Review next session\n\n**Next Session:** One week",
})

// Simple flat narrative (no **Label:** markers)
const plainNarrative: SOAPNoteModel = createMockSOAPNote({
  subjective: "Patient reports feeling better",
  objective: "Patient appears calm",
  assessment: "Continued progress",
  plan: "Continue weekly sessions",
})

const mockSession = createMockSession({
  transcript: { format: "vtt", content: "Test" },
  soap_note: structuredNarrative,
})

const TOTAL_SUBFIELDS = Object.values(SECTION_SUBFIELDS)
  .reduce((sum, fields) => sum + fields.length, 0)

// ClinicalObservationForm adds 2 textareas + 4 text inputs (appearance, eye contact, psychomotor notes, attitude)
const CLINICAL_OBS_TEXTBOXES = 6

function renderViewer(overrides: Record<string, unknown> = {}) {
  const defaults = {
    soapNote: structuredNarrative,
    soapNoteEdited: null,
    sessionId: "session-123",
    session: mockSession,
    status: "pending_review" as const,
  }
  return render(<SOAPViewer {...defaults} {...overrides} />)
}

describe("SOAPViewer", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe("Document View", () => {
    it("renders all four SOAP section headers simultaneously", () => {
      renderViewer()

      expect(screen.getByText("Subjective")).toBeInTheDocument()
      expect(screen.getByText("Objective")).toBeInTheDocument()
      expect(screen.getByText("Assessment")).toBeInTheDocument()
      expect(screen.getByText("Plan")).toBeInTheDocument()
    })

    it("renders sections in S-O-A-P order", () => {
      const { container } = renderViewer()

      const headings = container.querySelectorAll("h4")
      const labels = Array.from(headings).map((h) => h.textContent)
      expect(labels).toEqual(["Subjective", "Objective", "Assessment", "Plan"])
    })

    it("displays all section content simultaneously without tab switching", () => {
      renderViewer()

      expect(screen.getByText("Anxiety about work")).toBeInTheDocument()
      expect(screen.getByText("Well-groomed")).toBeInTheDocument()
      expect(screen.getByText("Improving")).toBeInTheDocument()
      expect(screen.getByText("One week")).toBeInTheDocument()
    })

    it("renders sub-field labels as headings from narrative format", () => {
      renderViewer()

      expect(screen.getByText("Chief Complaint")).toBeInTheDocument()
      expect(screen.getByText("Mood/Affect")).toBeInTheDocument()
      expect(screen.getByText("Clinical Impression")).toBeInTheDocument()
      expect(screen.getByText("Risk Assessment")).toBeInTheDocument()
    })

    it("renders plain text without sub-field headings", () => {
      renderViewer({ soapNote: plainNarrative })

      expect(screen.getByText("Patient reports feeling better")).toBeInTheDocument()
      expect(screen.queryByText("Chief Complaint")).not.toBeInTheDocument()
    })

    it("shows empty state when no SOAP note", () => {
      renderViewer({ soapNote: null })

      expect(screen.getByText("SOAP note not yet generated")).toBeInTheDocument()
    })

    it("displays AI Generated badge when no edited version", () => {
      renderViewer()

      expect(screen.getByText("AI Generated")).toBeInTheDocument()
    })

    it("displays Edited badge when edited version exists", () => {
      renderViewer({
        soapNoteEdited: structuredNarrative,
        status: "finalized",
      })

      expect(screen.getByText("Edited")).toBeInTheDocument()
    })

    it("displays edited version when both original and edited exist", () => {
      const edited = createMockSOAPNote({ subjective: "Edited subjective" })
      renderViewer({ soapNoteEdited: edited })

      expect(screen.getByText("Edited subjective")).toBeInTheDocument()
    })

    it("shows 'Based on transcript' zone in the Objective section", () => {
      renderViewer()

      expect(screen.getByText("Based on transcript")).toBeInTheDocument()
    })

    it("renders ClinicalObservationForm alongside the transcript zone", () => {
      renderViewer()

      expect(screen.getByText("Based on transcript")).toBeInTheDocument()
      expect(screen.getByText("Clinician Observations")).toBeInTheDocument()
    })
  })

  describe("Sub-Field Editing", () => {
    it("shows sub-field textareas when entering edit mode", () => {
      renderViewer()
      fireEvent.click(screen.getByText("Edit"))

      const textareas = screen.getAllByRole("textbox")
      expect(textareas).toHaveLength(TOTAL_SUBFIELDS + CLINICAL_OBS_TEXTBOXES)
    })

    it("shows labeled sub-fields for each SOAP section", () => {
      renderViewer()
      fireEvent.click(screen.getByText("Edit"))

      // Subjective sub-fields
      expect(screen.getByText("Chief Complaint")).toBeInTheDocument()
      expect(screen.getByText("Mood/Affect")).toBeInTheDocument()
      expect(screen.getByText("Symptoms")).toBeInTheDocument()
      expect(screen.getByText("Client Narrative")).toBeInTheDocument()

      // Objective sub-fields (Appearance appears twice: SubFieldEditor + ClinicalObsForm)
      expect(screen.getAllByText("Appearance").length).toBeGreaterThanOrEqual(1)
      expect(screen.getByText("Behavior")).toBeInTheDocument()
      expect(screen.getByText("Thought Process")).toBeInTheDocument()

      // Assessment sub-fields
      expect(screen.getByText("Clinical Impression")).toBeInTheDocument()
      expect(screen.getByText("Risk Assessment")).toBeInTheDocument()
      expect(screen.getByText("Functioning Level")).toBeInTheDocument()

      // Plan sub-fields
      expect(screen.getByText("Interventions Used")).toBeInTheDocument()
      expect(screen.getByText("Homework Assignments")).toBeInTheDocument()
      expect(screen.getByText("Next Steps")).toBeInTheDocument()
      expect(screen.getByText("Next Session")).toBeInTheDocument()
    })

    it("populates sub-fields from narrative **Label:** text", () => {
      renderViewer()
      fireEvent.click(screen.getByText("Edit"))

      const textareas = screen.getAllByRole("textbox")
      // chief_complaint is first sub-field of subjective
      expect(textareas[0]).toHaveValue("Anxiety about work")
      // mood_affect is second
      expect(textareas[1]).toHaveValue("Anxious but hopeful")
    })

    it("renders list fields as one item per line", () => {
      renderViewer()
      fireEvent.click(screen.getByText("Edit"))

      const textareas = screen.getAllByRole("textbox")
      // symptoms is 3rd sub-field of subjective (index 2)
      expect(textareas[2]).toHaveValue("Insomnia\nRacing thoughts")
    })

    it("populates plain text into catch-all sub-field", () => {
      renderViewer({ soapNote: plainNarrative })
      fireEvent.click(screen.getByText("Edit"))

      const textareas = screen.getAllByRole("textbox")
      // Plain subjective text falls back to client_narrative (index 3)
      expect(textareas[3]).toHaveValue("Patient reports feeling better")
    })

    it("produces correct narrative output on save", () => {
      const mockOnSave = vi.fn()
      renderViewer({ onSave: mockOnSave })

      fireEvent.click(screen.getByText("Edit"))
      fireEvent.click(screen.getByText("Save Changes"))

      expect(mockOnSave).toHaveBeenCalledTimes(1)
      const saved = mockOnSave.mock.calls[0][0] as SOAPNoteModel
      expect(saved.subjective).toContain("**Chief Complaint:** Anxiety about work")
      expect(saved.subjective).toContain("**Symptoms:**\n- Insomnia\n- Racing thoughts")
      expect(saved.objective).toContain("**Behavior:** Cooperative")
      expect(saved.assessment).toContain("**Risk Assessment:** No concerns")
      expect(saved.plan).toContain("**Next Session:** One week")
    })

    it("preserves content through narrative-to-structured round-trip", () => {
      const structured = narrativeToStructured(structuredNarrative)
      const roundTripped = structuredToNarrative(structured)

      expect(roundTripped.subjective).toContain("Anxiety about work")
      expect(roundTripped.subjective).toContain("Insomnia")
      expect(roundTripped.subjective).toContain("Racing thoughts")
      expect(roundTripped.objective).toContain("Well-groomed")
      expect(roundTripped.assessment).toContain("No concerns")
      expect(roundTripped.plan).toContain("One week")
    })

    it("reflects edits in saved output", () => {
      const mockOnSave = vi.fn()
      renderViewer({ onSave: mockOnSave })

      fireEvent.click(screen.getByText("Edit"))

      // Change the chief complaint (first textarea)
      const textareas = screen.getAllByRole("textbox")
      fireEvent.change(textareas[0], { target: { value: "Depression symptoms" } })

      fireEvent.click(screen.getByText("Save Changes"))

      const saved = mockOnSave.mock.calls[0][0] as SOAPNoteModel
      expect(saved.subjective).toContain("**Chief Complaint:** Depression symptoms")
    })

    it("exits edit mode after saving", () => {
      renderViewer()

      fireEvent.click(screen.getByText("Edit"))
      expect(screen.getByText("Save Changes")).toBeInTheDocument()

      fireEvent.click(screen.getByText("Save Changes"))
      expect(screen.queryByText("Save Changes")).not.toBeInTheDocument()
    })
  })

  describe("Clinician Observations", () => {
    it("renders form in Objective section for pending_review", () => {
      renderViewer()

      expect(screen.getByText("Clinician Observations")).toBeInTheDocument()
    })

    it("hides form when status is finalized", () => {
      renderViewer({ status: "finalized" })

      expect(screen.queryByText("Clinician Observations")).not.toBeInTheDocument()
    })

    it("hides form when readonly", () => {
      renderViewer({ readonly: true })

      expect(screen.queryByText("Clinician Observations")).not.toBeInTheDocument()
    })

    it("renders form in both view and edit modes", () => {
      renderViewer()
      expect(screen.getByText("Clinician Observations")).toBeInTheDocument()

      fireEvent.click(screen.getByText("Edit"))
      expect(screen.getByText("Clinician Observations")).toBeInTheDocument()
    })
  })

  describe("Save & Cancel", () => {
    it("shows Save Changes and Cancel buttons in edit mode", () => {
      renderViewer()

      fireEvent.click(screen.getByText("Edit"))

      expect(screen.getByText("Save Changes")).toBeInTheDocument()
      expect(screen.getByText("Cancel")).toBeInTheDocument()
    })

    it("cancels without dialog when no changes made", () => {
      renderViewer()

      fireEvent.click(screen.getByText("Edit"))
      fireEvent.click(screen.getByText("Cancel"))

      expect(screen.queryByText("Unsaved Changes")).not.toBeInTheDocument()
      expect(screen.queryByText("Save Changes")).not.toBeInTheDocument()
    })

    it("shows confirmation dialog when canceling with changes", async () => {
      renderViewer()

      fireEvent.click(screen.getByText("Edit"))

      const textarea = screen.getAllByRole("textbox")[0]
      fireEvent.change(textarea, { target: { value: "Changed" } })

      fireEvent.click(screen.getByText("Cancel"))

      await waitFor(() => {
        expect(screen.getByText("Unsaved Changes")).toBeInTheDocument()
      })
    })

    it("returns to edit mode when Keep Editing clicked", async () => {
      renderViewer()

      fireEvent.click(screen.getByText("Edit"))

      const textarea = screen.getAllByRole("textbox")[0]
      fireEvent.change(textarea, { target: { value: "Changed" } })

      fireEvent.click(screen.getByText("Cancel"))

      await waitFor(() => {
        expect(screen.getByText("Keep Editing")).toBeInTheDocument()
      })

      fireEvent.click(screen.getByText("Keep Editing"))

      await waitFor(() => {
        expect(screen.queryByText("Unsaved Changes")).not.toBeInTheDocument()
      })
      expect(screen.getByText("Save Changes")).toBeInTheDocument()
    })

    it("discards changes and exits edit mode when confirmed", async () => {
      renderViewer()

      fireEvent.click(screen.getByText("Edit"))

      const textarea = screen.getAllByRole("textbox")[0]
      fireEvent.change(textarea, { target: { value: "Changed" } })

      fireEvent.click(screen.getByText("Cancel"))

      await waitFor(() => {
        expect(screen.getByText("Discard Changes")).toBeInTheDocument()
      })

      fireEvent.click(screen.getByText("Discard Changes"))

      await waitFor(() => {
        expect(screen.queryByText("Save Changes")).not.toBeInTheDocument()
      })
    })
  })

  describe("PDF Export", () => {
    it("calls exportSOAPToPDF with session and note", async () => {
      const { exportSOAPToPDF } = await import("@/lib/utils/pdfExport")
      renderViewer()

      fireEvent.click(screen.getByText("Export PDF"))

      expect(exportSOAPToPDF).toHaveBeenCalledWith(mockSession, structuredNarrative)
    })

    it("uses edited note for export when available", async () => {
      const { exportSOAPToPDF } = await import("@/lib/utils/pdfExport")
      const edited = createMockSOAPNote({ subjective: "Edited" })
      renderViewer({ soapNoteEdited: edited })

      fireEvent.click(screen.getByText("Export PDF"))

      expect(exportSOAPToPDF).toHaveBeenCalledWith(mockSession, edited)
    })

    it("shows Export PDF button regardless of status", () => {
      renderViewer({ status: "finalized" })

      expect(screen.getByText("Export PDF")).toBeInTheDocument()
    })
  })

  describe("Readonly & Status", () => {
    it("shows Edit button for pending_review status", () => {
      renderViewer({ status: "pending_review" })

      expect(screen.getByText("Edit")).toBeInTheDocument()
    })

    it("hides Edit button for finalized status", () => {
      renderViewer({ status: "finalized" })

      expect(screen.queryByText("Edit")).not.toBeInTheDocument()
    })

    it("hides Edit button in readonly mode", () => {
      renderViewer({ readonly: true })

      expect(screen.queryByText("Edit")).not.toBeInTheDocument()
    })

    const nonEditableStatuses = ["queued", "processing", "failed"] as const
    nonEditableStatuses.forEach((status) => {
      it(`hides Edit button for ${status} status`, () => {
        renderViewer({ status })

        expect(screen.queryByText("Edit")).not.toBeInTheDocument()
      })
    })
  })

  describe("Source Verification Indicators", () => {
    const structuredNote = createMockStructuredSOAPNote()
    const sessionWithStructured = createMockSession({
      transcript: { format: "vtt", content: "Test" },
      soap_note: structuredNarrative,
      soap_note_structured: structuredNote,
    })

    function renderStructuredViewer(overrides: Record<string, unknown> = {}) {
      return renderViewer({
        session: sessionWithStructured,
        ...overrides,
      })
    }

    it("shows '(unverified)' for claims with no source references", () => {
      renderStructuredViewer()

      const unverifiedBadges = screen.getAllByText("(unverified)")
      expect(unverifiedBadges.length).toBeGreaterThan(0)
    })

    it("shows 'sourced' for claims with source references", () => {
      renderStructuredViewer()

      const sourcedBadges = screen.getAllByText("sourced")
      expect(sourcedBadges.length).toBeGreaterThan(0)
    })

    it("shows unverified indicator for objective.appearance (no sources)", () => {
      renderStructuredViewer()

      // appearance has no source_segment_ids in the factory
      expect(screen.getByText("Well-groomed, appropriately dressed.")).toBeInTheDocument()
      const unverifiedBadges = screen.getAllByText("(unverified)")
      expect(unverifiedBadges.length).toBeGreaterThan(0)
    })

    it("shows sourced indicator for subjective.chief_complaint (has sources)", () => {
      renderStructuredViewer()

      // chief_complaint has source_segment_ids [0, 1] in the factory
      expect(screen.getByText("Patient reports feeling anxious.")).toBeInTheDocument()
      const sourcedBadges = screen.getAllByText("sourced")
      expect(sourcedBadges.length).toBeGreaterThan(0)
    })

    it("does not show source indicators in edit mode", () => {
      renderStructuredViewer()

      fireEvent.click(screen.getByText("Edit"))

      expect(screen.queryByText("(unverified)")).not.toBeInTheDocument()
      expect(screen.queryByText("sourced")).not.toBeInTheDocument()
    })

    it("falls back to NarrativeContent when no structured data", () => {
      // Default mockSession has soap_note_structured: null
      renderViewer()

      // Should render via NarrativeContent — no source indicators
      expect(screen.queryByText("(unverified)")).not.toBeInTheDocument()
      expect(screen.queryByText("sourced")).not.toBeInTheDocument()
      // But content still renders from narrative
      expect(screen.getByText("Anxiety about work")).toBeInTheDocument()
    })

    it("applies yellow highlight styling to unverified claims", () => {
      const { container } = renderStructuredViewer()

      const highlighted = container.querySelectorAll(".border-yellow-400")
      expect(highlighted.length).toBeGreaterThan(0)
    })

    it("does not apply yellow highlight to verified claims", () => {
      const { container } = renderStructuredViewer()

      // Verified claims should not have yellow border
      // chief_complaint is verified — check its content is not in a yellow highlight
      const allHighlighted = container.querySelectorAll(".border-yellow-400")
      const highlightedTexts = Array.from(allHighlighted).map((el) => el.textContent)
      const verifiedText = "Patient reports feeling anxious."
      const hasVerifiedInHighlight = highlightedTexts.some((t) => t?.includes(verifiedText))
      expect(hasVerifiedInHighlight).toBe(false)
    })
  })

  describe("Claim Click Handlers", () => {
    const structuredNote = createMockStructuredSOAPNote()
    const sessionWithStructured = createMockSession({
      transcript: { format: "vtt", content: "Test" },
      soap_note: structuredNarrative,
      soap_note_structured: structuredNote,
    })

    function renderClickableViewer(overrides: Record<string, unknown> = {}) {
      return renderViewer({
        session: sessionWithStructured,
        ...overrides,
      })
    }

    it("calls onClaimClick with source_segment_ids when verified claim is clicked", () => {
      const mockOnClaimClick = vi.fn()
      renderClickableViewer({ onClaimClick: mockOnClaimClick })

      // chief_complaint has source_segment_ids [0, 1]
      const claimText = screen.getByText("Patient reports feeling anxious.")
      const clickable = claimText.closest("[role='button']")
      expect(clickable).not.toBeNull()
      fireEvent.click(clickable!)

      expect(mockOnClaimClick).toHaveBeenCalledWith([0, 1])
    })

    it("does not make unverified claims clickable", () => {
      const mockOnClaimClick = vi.fn()
      renderClickableViewer({ onClaimClick: mockOnClaimClick })

      // appearance has no source_segment_ids — should not be clickable
      const unverifiedText = screen.getByText("Well-groomed, appropriately dressed.")
      const wrapper = unverifiedText.closest("[role='button']")
      expect(wrapper).toBeNull()
    })

    it("does not render click handlers when onClaimClick is not provided", () => {
      const { container } = renderClickableViewer()

      // No role="button" on any claims since no onClaimClick
      const buttons = container.querySelectorAll("[role='button']")
      expect(buttons).toHaveLength(0)
    })

    it("does not render click handlers in edit mode", () => {
      const mockOnClaimClick = vi.fn()
      renderClickableViewer({ onClaimClick: mockOnClaimClick })

      fireEvent.click(screen.getByText("Edit"))

      const buttons = screen.queryAllByRole("button")
      // Only real buttons (Save, Cancel, Export PDF), not claim buttons
      const claimButtons = buttons.filter(
        (b) => !b.textContent?.match(/Save|Cancel|Export|Edit/)
      )
      expect(claimButtons).toHaveLength(0)
    })

    it("supports keyboard activation on verified claims", () => {
      const mockOnClaimClick = vi.fn()
      renderClickableViewer({ onClaimClick: mockOnClaimClick })

      const claimText = screen.getByText("Patient reports feeling anxious.")
      const clickable = claimText.closest("[role='button']")
      expect(clickable).not.toBeNull()

      fireEvent.keyDown(clickable!, { key: "Enter" })
      expect(mockOnClaimClick).toHaveBeenCalledWith([0, 1])

      mockOnClaimClick.mockClear()
      fireEvent.keyDown(clickable!, { key: " " })
      expect(mockOnClaimClick).toHaveBeenCalledWith([0, 1])
    })
  })
})
