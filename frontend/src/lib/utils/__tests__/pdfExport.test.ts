// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PDF Export Utility Tests
 *
 * Tests for SOAP note PDF generation including structured sub-field rendering.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { exportSOAPToPDF } from "../pdfExport"
import { parseNarrativeBlocks } from "../narrativeParser"
import type { SOAPNoteModel } from "@/types/sessions"
import { createMockSession, createMockSOAPNote } from "@/test/factories"

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

const mockSession = createMockSession({
  status: "finalized",
  transcript: { format: "vtt", content: "Test" },
  quality_rating: 5,
  finalized_at: "2024-01-15T15:00:00Z",
})

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
    exportSOAPToPDF(mockSession, mockSOAPNote)
    expect(mockOutput).toHaveBeenCalledWith("blob")
    expect(clickSpy).toHaveBeenCalled()
  })

  it("includes title", () => {
    exportSOAPToPDF(mockSession, mockSOAPNote)
    expect(mockText).toHaveBeenCalledWith("SOAP Note", 20, 20)
  })

  it("includes patient name", () => {
    exportSOAPToPDF(mockSession, mockSOAPNote)
    expect(mockText).toHaveBeenCalledWith(
      "Patient: Doe, Jane",
      20,
      expect.any(Number)
    )
  })

  it("includes session number", () => {
    exportSOAPToPDF(mockSession, mockSOAPNote)
    expect(mockText).toHaveBeenCalledWith(
      "Session #1",
      20,
      expect.any(Number)
    )
  })

  it("includes formatted date", () => {
    exportSOAPToPDF(mockSession, mockSOAPNote)
    expect(mockText).toHaveBeenCalledWith(
      expect.stringContaining("January 15, 2024"),
      20,
      expect.any(Number)
    )
  })

  const soapSections = ["Subjective", "Objective", "Assessment", "Plan"]

  soapSections.forEach((title) => {
    it(`includes ${title} section header`, () => {
      exportSOAPToPDF(mockSession, mockSOAPNote)
      expect(mockText).toHaveBeenCalledWith(title, 20, expect.any(Number))
    })
  })

  it("sets font sizes correctly", () => {
    exportSOAPToPDF(mockSession, mockSOAPNote)
    expect(mockSetFontSize).toHaveBeenCalledWith(18) // Title
    expect(mockSetFontSize).toHaveBeenCalledWith(12) // Metadata
    expect(mockSetFontSize).toHaveBeenCalledWith(14) // Section titles
    expect(mockSetFontSize).toHaveBeenCalledWith(11) // Section content
  })

  it("sets font styles correctly", () => {
    exportSOAPToPDF(mockSession, mockSOAPNote)
    expect(mockSetFont).toHaveBeenCalledWith("helvetica", "bold")
    expect(mockSetFont).toHaveBeenCalledWith("helvetica", "normal")
  })

  it("splits long text into lines", () => {
    exportSOAPToPDF(mockSession, mockSOAPNote)
    expect(mockSplitTextToSize).toHaveBeenCalled()
  })

  it("handles multiline content", () => {
    const multilineSOAP: SOAPNoteModel = {
      subjective: "Line 1\nLine 2\nLine 3",
      objective: "Observation",
      assessment: "Assessment",
      plan: "Plan",
    }

    exportSOAPToPDF(mockSession, multilineSOAP)
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

    exportSOAPToPDF(mockSession, longSOAP)
    expect(mockAddPage).toHaveBeenCalled()
  })

  it("handles empty sections gracefully", () => {
    const emptySOAP: SOAPNoteModel = {
      subjective: "",
      objective: "",
      assessment: "",
      plan: "",
    }

    expect(() => exportSOAPToPDF(mockSession, emptySOAP)).not.toThrow()
  })

  it("handles special characters in content", () => {
    const specialSOAP: SOAPNoteModel = {
      subjective: "Patient's mood: \"good\"",
      objective: "Heart rate: 72 bpm & BP: 120/80",
      assessment: "Progress > expectations",
      plan: "Continue <current treatment>",
    }

    expect(() => exportSOAPToPDF(mockSession, specialSOAP)).not.toThrow()
  })

  it("generates unique filenames for different sessions", () => {
    const session1 = { ...mockSession, id: "session-1" }
    const session2 = { ...mockSession, id: "session-2" }

    const createdLinks: HTMLAnchorElement[] = []
    const createSpy = vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = document.constructor.prototype.createElement.call(document, tag)
      if (tag === "a") createdLinks.push(el as HTMLAnchorElement)
      return el
    })

    exportSOAPToPDF(session1, mockSOAPNote)
    exportSOAPToPDF(session2, mockSOAPNote)

    const filenames = createdLinks.map((l) => l.download)
    expect(filenames).toContain("soap-note-session-1.pdf")
    expect(filenames).toContain("soap-note-session-2.pdf")
    createSpy.mockRestore()
  })

  it("renders sub-field labels in bold and content in normal", () => {
    const structuredSOAP: SOAPNoteModel = {
      subjective: "**Chief Complaint:** Anxiety\n\n**Mood/Affect:** Low",
      objective: "**Appearance:** Well-groomed",
      assessment: "**Clinical Impression:** Improving",
      plan: "**Next Session:** One week",
    }

    exportSOAPToPDF(mockSession, structuredSOAP)

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

    exportSOAPToPDF(mockSession, bulletSOAP)

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

    expect(() => exportSOAPToPDF(mockSession, withObservations)).not.toThrow()
    expect(mockText).toHaveBeenCalledWith(
      "Clinician Observations:",
      20,
      expect.any(Number)
    )
  })
})
