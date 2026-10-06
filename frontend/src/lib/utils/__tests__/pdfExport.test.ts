// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PDF Export Utility Tests
 *
 * Tests for SOAP note PDF generation including structured sub-field rendering.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { exportNoteToPDF, exportSOAPToPDF, type PDFExportMetadata, type PDFNote } from "../pdfExport"
import { parseNarrativeBlocks } from "../narrativeParser"
import type { SOAPNoteModel } from "@/types/sessions"
import { createMockSOAPNote } from "@/test/factories"
import { peopleWords } from "@/lib/peopleTerm"

// Mock jsPDF
let mockOutput: ReturnType<typeof vi.fn>
let mockAddPage: ReturnType<typeof vi.fn>
let mockText: ReturnType<typeof vi.fn>
let mockSetFontSize: ReturnType<typeof vi.fn>
let mockSetFont: ReturnType<typeof vi.fn>
let mockSplitTextToSize: ReturnType<typeof vi.fn>

vi.mock("jspdf", () => ({
  default: class {
    output = mockOutput
    addPage = mockAddPage
    text = mockText
    setFontSize = mockSetFontSize
    setFont = mockSetFont
    splitTextToSize = mockSplitTextToSize
    internal = {
      pageSize: {
        height: 297, // A4 height in mm
      },
    }
  },
}))

// Mock URL APIs for download
vi.stubGlobal("URL", {
  createObjectURL: vi.fn(() => "blob:mock-url"),
  revokeObjectURL: vi.fn(),
})

const mockSession: PDFExportMetadata = {
  patient_name: "Doe, Jane",
  session_number: 1,
  session_date: "2024-01-15T14:30:00Z",
}

const mockSOAPNote: SOAPNoteModel = createMockSOAPNote({
  subjective: "Patient reports feeling better",
  objective: "Patient appears calm and engaged",
  assessment: "Continued progress in therapy",
  plan: "Continue weekly sessions",
})

describe("parseNarrativeBlocks", () => {
  it("returns empty array for empty text", () => {
    expect(parseNarrativeBlocks("")).toEqual([])
    expect(parseNarrativeBlocks("   ")).toEqual([])
  })

  it("returns plain text block when no labels found", () => {
    const result = parseNarrativeBlocks("Just plain text here")
    expect(result).toEqual([{ label: null, content: "Just plain text here" }])
  })

  it("parses single **Label:** content pattern", () => {
    const result = parseNarrativeBlocks("**Appearance:** Well-groomed")
    expect(result).toEqual([{ label: "Appearance", content: "Well-groomed" }])
  })

  it("parses multiple **Label:** content patterns", () => {
    const text = "**Appearance:** Well-groomed\n\n**Behavior:** Cooperative"
    const result = parseNarrativeBlocks(text)
    expect(result).toHaveLength(2)
    expect(result[0]).toEqual({ label: "Appearance", content: "Well-groomed" })
    expect(result[1]).toEqual({ label: "Behavior", content: "Cooperative" })
  })

  it("handles bullet list content under a label", () => {
    const text = "**Symptoms:**\n- Anxiety\n- Insomnia"
    const result = parseNarrativeBlocks(text)
    expect(result).toHaveLength(1)
    expect(result[0].label).toBe("Symptoms")
    expect(result[0].content).toContain("- Anxiety")
    expect(result[0].content).toContain("- Insomnia")
  })

  it("handles Clinician Observations label", () => {
    const text = "**Clinician Observations:**\n\n**Appearance:** Disheveled"
    const result = parseNarrativeBlocks(text)
    expect(result.some((b) => b.label === "Clinician Observations")).toBe(true)
    expect(result.some((b) => b.label === "Appearance")).toBe(true)
  })

  it("preserves leading text before first label", () => {
    const text = "Some preamble\n\n**Label:** content"
    const result = parseNarrativeBlocks(text)
    expect(result[0]).toEqual({ label: null, content: "Some preamble" })
    expect(result[1]).toEqual({ label: "Label", content: "content" })
  })
})

describe("exportSOAPToPDF", () => {
  let clickSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    mockOutput = vi.fn(() => new Blob(["pdf"], { type: "application/pdf" }))
    mockAddPage = vi.fn()
    mockText = vi.fn()
    mockSetFontSize = vi.fn()
    mockSetFont = vi.fn()
    mockSplitTextToSize = vi.fn((text: string) => text.split("\n"))
    clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {})
    vi.clearAllMocks()
  })

  afterEach(() => {
    clickSpy.mockRestore()
  })

  it("generates PDF with correct filename", () => {
    exportSOAPToPDF(mockSession, mockSOAPNote, peopleWords("patients"))
    expect(mockOutput).toHaveBeenCalledWith("blob")
    expect(clickSpy).toHaveBeenCalled()
  })

  it("includes title", () => {
    exportSOAPToPDF(mockSession, mockSOAPNote, peopleWords("patients"))
    expect(mockText).toHaveBeenCalledWith("SOAP Note", 20, 20)
  })

  it("includes patient name", () => {
    exportSOAPToPDF(mockSession, mockSOAPNote, peopleWords("patients"))
    expect(mockText).toHaveBeenCalledWith(
      "Patient: Doe, Jane",
      20,
      expect.any(Number)
    )
  })

  it("includes session number", () => {
    exportSOAPToPDF(mockSession, mockSOAPNote, peopleWords("patients"))
    expect(mockText).toHaveBeenCalledWith(
      "Session #1",
      20,
      expect.any(Number)
    )
  })

  it("includes formatted date", () => {
    exportSOAPToPDF(mockSession, mockSOAPNote, peopleWords("patients"))
    expect(mockText).toHaveBeenCalledWith(
      expect.stringContaining("January 15, 2024"),
      20,
      expect.any(Number)
    )
  })

  const soapSections = ["Subjective", "Objective", "Assessment", "Plan"]

  soapSections.forEach((title) => {
    it(`includes ${title} section header`, () => {
      exportSOAPToPDF(mockSession, mockSOAPNote, peopleWords("patients"))
      expect(mockText).toHaveBeenCalledWith(title, 20, expect.any(Number))
    })
  })

  it("sets font sizes correctly", () => {
    exportSOAPToPDF(mockSession, mockSOAPNote, peopleWords("patients"))
    expect(mockSetFontSize).toHaveBeenCalledWith(18) // Title
    expect(mockSetFontSize).toHaveBeenCalledWith(12) // Metadata
    expect(mockSetFontSize).toHaveBeenCalledWith(14) // Section titles
    expect(mockSetFontSize).toHaveBeenCalledWith(11) // Section content
  })

  it("sets font styles correctly", () => {
    exportSOAPToPDF(mockSession, mockSOAPNote, peopleWords("patients"))
    expect(mockSetFont).toHaveBeenCalledWith("helvetica", "bold")
    expect(mockSetFont).toHaveBeenCalledWith("helvetica", "normal")
  })

  it("splits long text into lines", () => {
    exportSOAPToPDF(mockSession, mockSOAPNote, peopleWords("patients"))
    expect(mockSplitTextToSize).toHaveBeenCalled()
  })

  it("handles multiline content", () => {
    const multilineSOAP: SOAPNoteModel = {
      subjective: "Line 1\nLine 2\nLine 3",
      objective: "Observation",
      assessment: "Assessment",
      plan: "Plan",
    }

    exportSOAPToPDF(mockSession, multilineSOAP, peopleWords("patients"))
    expect(mockSplitTextToSize).toHaveBeenCalled()
  })

  it("adds new page when content is long", () => {
    const longContent = "Long content\n".repeat(100)
    const longSOAP: SOAPNoteModel = {
      subjective: longContent,
      objective: longContent,
      assessment: longContent,
      plan: longContent,
    }

    exportSOAPToPDF(mockSession, longSOAP, peopleWords("patients"))
    expect(mockAddPage).toHaveBeenCalled()
  })

  it("handles empty sections gracefully", () => {
    const emptySOAP: SOAPNoteModel = {
      subjective: "",
      objective: "",
      assessment: "",
      plan: "",
    }

    expect(() => exportSOAPToPDF(mockSession, emptySOAP, peopleWords("patients"))).not.toThrow()
  })

  it("handles special characters in content", () => {
    const specialSOAP: SOAPNoteModel = {
      subjective: "Patient's mood: \"good\"",
      objective: "Heart rate: 72 bpm & BP: 120/80",
      assessment: "Progress > expectations",
      plan: "Continue <current treatment>",
    }

    expect(() => exportSOAPToPDF(mockSession, specialSOAP, peopleWords("patients"))).not.toThrow()
  })

  it("generates filenames derived from patient name and date", () => {
    const meta1: PDFExportMetadata = { ...mockSession, patient_name: "Smith, Alice" }
    const meta2: PDFExportMetadata = { ...mockSession, patient_name: "Jones, Bob" }

    const createdLinks: HTMLAnchorElement[] = []
    const createSpy = vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = document.constructor.prototype.createElement.call(document, tag)
      if (tag === "a") createdLinks.push(el as HTMLAnchorElement)
      return el
    })

    exportSOAPToPDF(meta1, mockSOAPNote, peopleWords("patients"))
    exportSOAPToPDF(meta2, mockSOAPNote, peopleWords("patients"))

    const filenames = createdLinks.map((l) => l.download)
    expect(filenames.some((f) => f.includes("smith"))).toBe(true)
    expect(filenames.some((f) => f.includes("jones"))).toBe(true)
    createSpy.mockRestore()
  })

  it("renders sub-field labels in bold and content in normal", () => {
    const structuredSOAP: SOAPNoteModel = {
      subjective: "**Chief Complaint:** Anxiety\n\n**Mood/Affect:** Low",
      objective: "**Appearance:** Well-groomed",
      assessment: "**Clinical Impression:** Improving",
      plan: "**Next Session:** One week",
    }

    exportSOAPToPDF(mockSession, structuredSOAP, peopleWords("patients"))

    // Sub-field labels should be rendered bold
    expect(mockText).toHaveBeenCalledWith(
      "Chief Complaint:",
      20,
      expect.any(Number)
    )
    expect(mockText).toHaveBeenCalledWith(
      "Appearance:",
      20,
      expect.any(Number)
    )
  })

  it("renders bullet list items with indentation", () => {
    const bulletSOAP: SOAPNoteModel = {
      subjective: "**Symptoms:**\n- Anxiety\n- Insomnia",
      objective: "Normal",
      assessment: "Stable",
      plan: "Continue",
    }

    exportSOAPToPDF(mockSession, bulletSOAP, peopleWords("patients"))

    // Bullet items should be rendered at indented x-position (28)
    expect(mockText).toHaveBeenCalledWith(
      expect.stringContaining("Anxiety"),
      28,
      expect.any(Number)
    )
  })

  it("does not throw with clinician observations in Objective", () => {
    const withObservations: SOAPNoteModel = {
      subjective: "**Chief Complaint:** Anxiety",
      objective:
        "**Appearance:** Calm\n\n**Clinician Observations:**\n\n**Eye Contact:** Appropriate\n**Non-verbal:** Relaxed posture",
      assessment: "**Clinical Impression:** Improving",
      plan: "**Next Session:** One week",
    }

    expect(() => exportSOAPToPDF(mockSession, withObservations, peopleWords("patients"))).not.toThrow()
    expect(mockText).toHaveBeenCalledWith(
      "Clinician Observations:",
      20,
      expect.any(Number)
    )
  })

  describe("signature block", () => {
    const printed = () => mockText.mock.calls.map((call) => call[0] as string)

    it("prints the block, the amendment line, then each addendum with its signature", () => {
      exportSOAPToPDF(
        {
          ...mockSession,
          signature: {
            lines: ["Electronically signed by Sam Ortiz, LMFT", "Oct 5, 2026, 3:04 PM EDT"],
            amendments: ["Amended Oct 6, 2026: Wrong date of service"],
            addenda: [
              {
                text: "Called after the session.",
                lines: ["Electronically signed by Sam Ortiz, LMFT", "Oct 7, 2026, 9:00 AM EDT"],
              },
            ],
          },
        },
        mockSOAPNote,
        peopleWords("patients"),
      )
      const text = printed()
      const at = (s: string) => text.indexOf(s)
      expect(at("Electronically signed by Sam Ortiz, LMFT")).toBeGreaterThan(at("Plan"))
      expect(at("Amended Oct 6, 2026: Wrong date of service")).toBeGreaterThan(
        at("Oct 5, 2026, 3:04 PM EDT"),
      )
      expect(at("Addendum")).toBeGreaterThan(at("Amended Oct 6, 2026: Wrong date of service"))
      expect(at("Called after the session.")).toBeGreaterThan(at("Addendum"))
      expect(text).toContain("Oct 7, 2026, 9:00 AM EDT")
    })

    it("prints no signature block for a note never signed", () => {
      exportSOAPToPDF(mockSession, mockSOAPNote, peopleWords("patients"))
      expect(printed().some((t) => t.startsWith("Electronically signed"))).toBe(false)
      expect(printed()).not.toContain("Addendum")
    })

    it("prints Finalized and no signature line for a note finalized before signatures", () => {
      exportSOAPToPDF(
        {
          ...mockSession,
          signature: { lines: ["Finalized Jan 15, 2024"], amendments: [], addenda: [] },
        },
        mockSOAPNote,
        peopleWords("patients"),
      )
      expect(printed()).toContain("Finalized Jan 15, 2024")
      expect(printed().some((t) => t.startsWith("Electronically signed"))).toBe(false)
    })
  })
})

describe("exportNoteToPDF", () => {
  let clickSpy: ReturnType<typeof vi.spyOn>
  const printed = () => mockText.mock.calls.map((call) => call[0] as string)

  const followUp: PDFNote = {
    title: "Psychiatric follow-up",
    sections: [
      {
        title: "Assessment",
        blocks: [
          { label: "Diagnoses", content: "- Major depressive disorder (F33.1), improving" },
          { label: "Formulation", content: "Mood steadier." },
        ],
      },
      { title: "Plan", blocks: [{ label: "Medication plan", content: "- Continue sertraline" }] },
    ],
  }

  beforeEach(() => {
    mockOutput = vi.fn(() => new Blob(["pdf"], { type: "application/pdf" }))
    mockAddPage = vi.fn()
    mockText = vi.fn()
    mockSetFontSize = vi.fn()
    mockSetFont = vi.fn()
    mockSplitTextToSize = vi.fn((text: string) => text.split("\n"))
    clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {})
    vi.clearAllMocks()
  })

  afterEach(() => {
    clickSpy.mockRestore()
  })

  it("prints the title, the visit's times, each section's fields in order, then the signature", () => {
    exportNoteToPDF(
      {
        ...mockSession,
        visit: [
          "Started 11:00 AM · Ended 11:55 AM · 55 min",
          "Psychotherapy time: 11:12 AM to 11:50 AM, 38 minutes · 38–52 minutes",
        ],
        signature: {
          lines: ["Electronically signed by Sam Ortiz, PMHNP-BC"],
          amendments: [],
          addenda: [],
        },
      },
      followUp,
      peopleWords("clients"),
    )
    const text = printed()
    const order = [
      "Psychiatric follow-up",
      "Client: Doe, Jane",
      "Started 11:00 AM · Ended 11:55 AM · 55 min",
      "Psychotherapy time: 11:12 AM to 11:50 AM, 38 minutes · 38–52 minutes",
      "Assessment",
      "Diagnoses:",
      "• Major depressive disorder (F33.1), improving",
      "Formulation:",
      "Mood steadier.",
      "Plan",
      "Medication plan:",
      "• Continue sertraline",
      "Electronically signed by Sam Ortiz, PMHNP-BC",
    ]
    const positions = order.map((line) => text.indexOf(line))
    expect(positions).not.toContain(-1)
    expect([...positions].sort((a, b) => a - b)).toEqual(positions)
  })

  it("names the file after the note type", () => {
    const createdLinks: HTMLAnchorElement[] = []
    const createSpy = vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = document.constructor.prototype.createElement.call(document, tag)
      if (tag === "a") createdLinks.push(el as HTMLAnchorElement)
      return el
    })
    exportNoteToPDF(mockSession, followUp, peopleWords("clients"))
    exportSOAPToPDF(mockSession, mockSOAPNote, peopleWords("clients"))
    expect(createdLinks.map((l) => l.download)).toEqual([
      "psychiatric-follow-up-doe-jane-2024-01-15.pdf",
      "soap-note-doe-jane-2024-01-15.pdf",
    ])
    createSpy.mockRestore()
  })

  it("prints no heading for a note that is one body", () => {
    exportNoteToPDF(
      mockSession,
      { title: "Narrative Note", sections: [{ title: "", blocks: [{ label: null, content: "Body" }] }] },
      peopleWords("clients"),
    )
    expect(printed()).toContain("Body")
    expect(printed()).not.toContain("")
  })
})
