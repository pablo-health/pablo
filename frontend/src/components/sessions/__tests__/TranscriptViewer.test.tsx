// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * TranscriptViewer Component Tests
 *
 * Comprehensive tests for transcript display, copying, expand/collapse,
 * segment-based rendering, and highlight functionality.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { TranscriptViewer, type TranscriptViewerHandle } from "../TranscriptViewer"
import type { TranscriptModel, TranscriptSegment } from "@/types/sessions"
import { createRef } from "react"

// Mock clipboard API
const mockWriteText = vi.fn().mockResolvedValue(undefined)
Object.defineProperty(navigator, "clipboard", {
  value: {
    writeText: mockWriteText,
  },
  writable: true,
})

const mockSegments: TranscriptSegment[] = [
  { index: 0, speaker: "Therapist", text: "How are you feeling today?", start_time: 1, end_time: 5 },
  { index: 1, speaker: "Client", text: "I'm feeling better than last week.", start_time: 6, end_time: 12 },
  { index: 2, speaker: "Therapist", text: "That's great to hear.", start_time: 13, end_time: 16 },
  { index: 3, speaker: "Client", text: "The breathing exercises helped a lot.", start_time: 17, end_time: 22 },
]

describe("TranscriptViewer", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe("Rendering", () => {
    it("renders transcript header", () => {
      const transcript: TranscriptModel = {
        format: "vtt",
        content: "Test content",
      }

      render(<TranscriptViewer transcript={transcript} />)

      expect(screen.getByText("Transcript")).toBeInTheDocument()
      expect(screen.getByText("Format: VTT")).toBeInTheDocument()
    })

    it("displays VTT format correctly", () => {
      const transcript: TranscriptModel = {
        format: "vtt",
        content: `WEBVTT

00:00:00.000 --> 00:00:05.000
Therapist: How are you feeling today?

00:00:05.001 --> 00:00:10.000
Patient: I'm feeling better.`,
      }

      render(<TranscriptViewer transcript={transcript} />)

      expect(screen.getByText(/Therapist: How are you feeling today?/)).toBeInTheDocument()
      expect(screen.getByText(/Patient: I'm feeling better./)).toBeInTheDocument()
      expect(screen.queryByText("WEBVTT")).not.toBeInTheDocument()
      expect(screen.queryByText("00:00:00.000")).not.toBeInTheDocument()
    })

    it("displays JSON format correctly", () => {
      const transcript: TranscriptModel = {
        format: "json",
        content: JSON.stringify([
          { text: "First line" },
          { text: "Second line" },
        ]),
      }

      render(<TranscriptViewer transcript={transcript} />)

      expect(screen.getByText(/First line/)).toBeInTheDocument()
      expect(screen.getByText(/Second line/)).toBeInTheDocument()
      expect(screen.getByText("Format: JSON")).toBeInTheDocument()
    })

    it("displays TXT format correctly", () => {
      const transcript: TranscriptModel = {
        format: "txt",
        content: "This is plain text\nWith multiple lines",
      }

      render(<TranscriptViewer transcript={transcript} />)

      expect(screen.getByText(/This is plain text/)).toBeInTheDocument()
      expect(screen.getByText(/With multiple lines/)).toBeInTheDocument()
      expect(screen.getByText("Format: TXT")).toBeInTheDocument()
    })

    it("displays format in uppercase", () => {
      const formats: Array<TranscriptModel["format"]> = ["vtt", "json", "txt"]

      formats.forEach((format) => {
        const { rerender } = render(
          <TranscriptViewer transcript={{ format, content: "Test" }} />
        )

        expect(screen.getByText(`Format: ${format.toUpperCase()}`)).toBeInTheDocument()

        rerender(<div />)
      })
    })
  })

  describe("Segment-Based Rendering", () => {
    it("renders individual segments when transcriptSegments provided", () => {
      render(
        <TranscriptViewer
          transcript={{ format: "vtt", content: "raw" }}
          transcriptSegments={mockSegments}
        />
      )

      expect(screen.getByTestId("segment-list")).toBeInTheDocument()
      expect(screen.getByText("How are you feeling today?")).toBeInTheDocument()
      expect(screen.getByText("I'm feeling better than last week.")).toBeInTheDocument()
      expect(screen.getByText("That's great to hear.")).toBeInTheDocument()
    })

    it("renders speaker labels for each segment", () => {
      render(
        <TranscriptViewer
          transcript={{ format: "vtt", content: "raw" }}
          transcriptSegments={mockSegments}
        />
      )

      const therapistLabels = screen.getAllByText("Therapist:")
      const clientLabels = screen.getAllByText("Client:")
      expect(therapistLabels).toHaveLength(2)
      expect(clientLabels).toHaveLength(2)
    })

    it("renders timestamps for each segment", () => {
      render(
        <TranscriptViewer
          transcript={{ format: "vtt", content: "raw" }}
          transcriptSegments={mockSegments}
        />
      )

      expect(screen.getByText("0:01")).toBeInTheDocument()
      expect(screen.getByText("0:06")).toBeInTheDocument()
      expect(screen.getByText("0:13")).toBeInTheDocument()
    })

    it("adds data-segment-index attribute to each segment", () => {
      const { container } = render(
        <TranscriptViewer
          transcript={{ format: "vtt", content: "raw" }}
          transcriptSegments={mockSegments}
        />
      )

      const segments = container.querySelectorAll("[data-segment-index]")
      expect(segments).toHaveLength(4)
      expect(segments[0]).toHaveAttribute("data-segment-index", "0")
      expect(segments[3]).toHaveAttribute("data-segment-index", "3")
    })

    it("falls back to raw text when no segments provided", () => {
      const { container } = render(
        <TranscriptViewer transcript={{ format: "txt", content: "Plain text content" }} />
      )

      expect(screen.queryByTestId("segment-list")).not.toBeInTheDocument()
      expect(container.querySelector("pre")).toBeInTheDocument()
      expect(screen.getByText("Plain text content")).toBeInTheDocument()
    })

    it("falls back to raw text when segments array is empty", () => {
      const { container } = render(
        <TranscriptViewer
          transcript={{ format: "txt", content: "Fallback text" }}
          transcriptSegments={[]}
        />
      )

      expect(screen.queryByTestId("segment-list")).not.toBeInTheDocument()
      expect(container.querySelector("pre")).toBeInTheDocument()
    })
  })

  describe("Segment Highlighting", () => {
    it("applies highlight styling to specified segments", () => {
      const { container } = render(
        <TranscriptViewer
          transcript={{ format: "vtt", content: "raw" }}
          transcriptSegments={mockSegments}
          highlightedSegments={[1, 3]}
        />
      )

      const seg0 = container.querySelector("[data-segment-index='0']")
      const seg1 = container.querySelector("[data-segment-index='1']")
      const seg3 = container.querySelector("[data-segment-index='3']")

      expect(seg0?.className).not.toContain("bg-blue-100")
      expect(seg1?.className).toContain("bg-blue-100")
      expect(seg3?.className).toContain("bg-blue-100")
    })

    it("does not highlight when highlightedSegments is empty", () => {
      const { container } = render(
        <TranscriptViewer
          transcript={{ format: "vtt", content: "raw" }}
          transcriptSegments={mockSegments}
          highlightedSegments={[]}
        />
      )

      const highlighted = container.querySelectorAll(".bg-blue-100")
      expect(highlighted).toHaveLength(0)
    })
  })

  describe("Scroll To Segment", () => {
    it("exposes scrollToSegment via ref", () => {
      const ref = createRef<TranscriptViewerHandle>()

      render(
        <TranscriptViewer
          ref={ref}
          transcript={{ format: "vtt", content: "raw" }}
          transcriptSegments={mockSegments}
        />
      )

      expect(ref.current).not.toBeNull()
      expect(typeof ref.current?.scrollToSegment).toBe("function")
    })
  })

  describe("Copy to Clipboard", () => {
    it("renders copy button", () => {
      const transcript: TranscriptModel = {
        format: "txt",
        content: "Test content",
      }

      render(<TranscriptViewer transcript={transcript} />)

      expect(screen.getByLabelText("Copy transcript to clipboard")).toBeInTheDocument()
      expect(screen.getByText("Copy")).toBeInTheDocument()
    })

    it("copies formatted content to clipboard when button is clicked", async () => {
      const transcript: TranscriptModel = {
        format: "vtt",
        content: `WEBVTT

00:00:00.000 --> 00:00:05.000
Test line`,
      }

      render(<TranscriptViewer transcript={transcript} />)

      const copyButton = screen.getByLabelText("Copy transcript to clipboard")
      fireEvent.click(copyButton)

      await waitFor(() => {
        expect(mockWriteText).toHaveBeenCalledWith("Test line")
      })
    })

    it("shows 'Copied' feedback after successful copy", async () => {
      const transcript: TranscriptModel = {
        format: "txt",
        content: "Test",
      }

      render(<TranscriptViewer transcript={transcript} />)

      const copyButton = screen.getByLabelText("Copy transcript to clipboard")
      fireEvent.click(copyButton)

      await waitFor(() => {
        expect(screen.getByText("Copied")).toBeInTheDocument()
      })
    })

    it("handles copy errors gracefully", async () => {
      const consoleError = vi.spyOn(console, "error").mockImplementation(() => {})
      mockWriteText.mockRejectedValueOnce(
        new Error("Permission denied")
      )

      const transcript: TranscriptModel = {
        format: "txt",
        content: "Test",
      }

      render(<TranscriptViewer transcript={transcript} />)

      const copyButton = screen.getByLabelText("Copy transcript to clipboard")
      fireEvent.click(copyButton)

      await waitFor(() => {
        expect(consoleError).toHaveBeenCalled()
      })

      consoleError.mockRestore()
    })
  })

  describe("Expand/Collapse", () => {
    it("shows expand button for long transcripts", () => {
      const longContent = "This is a long line with enough text to exceed the preview character limit.\n".repeat(10) // Long enough to trigger collapse
      const transcript: TranscriptModel = {
        format: "txt",
        content: longContent,
      }

      render(<TranscriptViewer transcript={transcript} />)

      expect(screen.getByLabelText("Expand transcript")).toBeInTheDocument()
      expect(screen.getByText("Show More")).toBeInTheDocument()
    })

    it("does not show expand button for short transcripts", () => {
      const transcript: TranscriptModel = {
        format: "txt",
        content: "Short content",
      }

      render(<TranscriptViewer transcript={transcript} />)

      expect(screen.queryByLabelText("Expand transcript")).not.toBeInTheDocument()
      expect(screen.queryByText("Show More")).not.toBeInTheDocument()
    })

    it("expands transcript when expand button is clicked", () => {
      const longContent = "This is a long line with enough text to exceed the preview character limit.\n".repeat(10)
      const transcript: TranscriptModel = {
        format: "txt",
        content: longContent,
      }

      render(<TranscriptViewer transcript={transcript} />)

      const expandButton = screen.getByLabelText("Expand transcript")
      fireEvent.click(expandButton)

      expect(screen.getByLabelText("Collapse transcript")).toBeInTheDocument()
      expect(screen.getByText("Show Less")).toBeInTheDocument()
      expect(expandButton).toHaveAttribute("aria-expanded", "true")
    })

    it("collapses transcript when collapse button is clicked", () => {
      const longContent = "This is a long line with enough text to exceed the preview character limit.\n".repeat(10)
      const transcript: TranscriptModel = {
        format: "txt",
        content: longContent,
      }

      render(<TranscriptViewer transcript={transcript} />)

      const expandButton = screen.getByLabelText("Expand transcript")
      fireEvent.click(expandButton) // Expand

      const collapseButton = screen.getByLabelText("Collapse transcript")
      fireEvent.click(collapseButton) // Collapse

      expect(screen.getByLabelText("Expand transcript")).toBeInTheDocument()
      expect(screen.getByText("Show More")).toBeInTheDocument()
    })

    it("toggles between expanded and collapsed states", () => {
      const longContent = "This is a long line with enough text to exceed the preview character limit.\n".repeat(10)
      const transcript: TranscriptModel = {
        format: "txt",
        content: longContent,
      }

      render(<TranscriptViewer transcript={transcript} />)

      const button = screen.getByLabelText("Expand transcript")

      // Expand
      fireEvent.click(button)
      expect(screen.getByText("Show Less")).toBeInTheDocument()

      // Collapse
      fireEvent.click(screen.getByLabelText("Collapse transcript"))
      expect(screen.getByText("Show More")).toBeInTheDocument()

      // Expand again
      fireEvent.click(screen.getByLabelText("Expand transcript"))
      expect(screen.getByText("Show Less")).toBeInTheDocument()
    })

    it("shows fade overlay when collapsed", () => {
      const longContent = "This is a long line with enough text to exceed the preview character limit.\n".repeat(10)
      const transcript: TranscriptModel = {
        format: "txt",
        content: longContent,
      }

      const { container } = render(<TranscriptViewer transcript={transcript} />)

      const overlay = container.querySelector(".bg-gradient-to-t")
      expect(overlay).toBeInTheDocument()
    })

    it("hides fade overlay when expanded", () => {
      const longContent = "This is a long line with enough text to exceed the preview character limit.\n".repeat(10)
      const transcript: TranscriptModel = {
        format: "txt",
        content: longContent,
      }

      const { container } = render(<TranscriptViewer transcript={transcript} />)

      const expandButton = screen.getByLabelText("Expand transcript")
      fireEvent.click(expandButton)

      const overlay = container.querySelector(".bg-gradient-to-t")
      expect(overlay).not.toBeInTheDocument()
    })

    it("shows expand button when many segments are provided", () => {
      const manySegments: TranscriptSegment[] = Array.from({ length: 12 }, (_, i) => ({
        index: i,
        speaker: i % 2 === 0 ? "Therapist" : "Client",
        text: `Segment ${i} text content`,
        start_time: i * 5,
        end_time: (i + 1) * 5,
      }))

      render(
        <TranscriptViewer
          transcript={{ format: "vtt", content: "raw" }}
          transcriptSegments={manySegments}
        />
      )

      expect(screen.getByLabelText("Expand transcript")).toBeInTheDocument()
    })
  })

  describe("Edge Cases", () => {
    it("handles empty transcript content", () => {
      const transcript: TranscriptModel = {
        format: "txt",
        content: "",
      }

      render(<TranscriptViewer transcript={transcript} />)

      expect(screen.getByText("Transcript")).toBeInTheDocument()
      expect(screen.queryByText("Show More")).not.toBeInTheDocument()
    })

    it("handles whitespace-only content", () => {
      const transcript: TranscriptModel = {
        format: "txt",
        content: "   \n   \n   ",
      }

      render(<TranscriptViewer transcript={transcript} />)

      expect(screen.getByText("Transcript")).toBeInTheDocument()
    })

    it("handles very long single line", () => {
      const transcript: TranscriptModel = {
        format: "txt",
        content: "A".repeat(1000),
      }

      render(<TranscriptViewer transcript={transcript} />)

      expect(screen.getByLabelText("Expand transcript")).toBeInTheDocument()
    })

    it("applies custom className", () => {
      const transcript: TranscriptModel = {
        format: "txt",
        content: "Test",
      }

      const { container } = render(
        <TranscriptViewer transcript={transcript} className="custom-class" />
      )

      const wrapper = container.firstChild as HTMLElement
      expect(wrapper.className).toContain("custom-class")
    })

    it("preserves newlines and formatting in display", () => {
      const transcript: TranscriptModel = {
        format: "txt",
        content: "Line 1\n\nLine 2\n  Indented line",
      }

      const { container } = render(<TranscriptViewer transcript={transcript} />)

      const pre = container.querySelector("pre")
      expect(pre?.textContent).toContain("Line 1")
      expect(pre?.textContent).toContain("Line 2")
      expect(pre?.textContent).toContain("  Indented line")
    })

    it("handles special characters in content", () => {
      const transcript: TranscriptModel = {
        format: "txt",
        content: "Special: <>&\"' ñ é 中文 🎉",
      }

      render(<TranscriptViewer transcript={transcript} />)

      expect(screen.getByText(/Special: <>&"' ñ é 中文 🎉/)).toBeInTheDocument()
    })
  })

  describe("Accessibility", () => {
    it("has accessible button labels", () => {
      const transcript: TranscriptModel = {
        format: "txt",
        content: "This is a long line with enough text to exceed the preview character limit.\n".repeat(10),
      }

      render(<TranscriptViewer transcript={transcript} />)

      expect(screen.getByLabelText("Copy transcript to clipboard")).toBeInTheDocument()
      expect(screen.getByLabelText("Expand transcript")).toBeInTheDocument()
    })

    it("sets aria-expanded attribute correctly", () => {
      const transcript: TranscriptModel = {
        format: "txt",
        content: "This is a long line with enough text to exceed the preview character limit.\n".repeat(10),
      }

      render(<TranscriptViewer transcript={transcript} />)

      const expandButton = screen.getByLabelText("Expand transcript")
      expect(expandButton).toHaveAttribute("aria-expanded", "false")

      fireEvent.click(expandButton)

      const collapseButton = screen.getByLabelText("Collapse transcript")
      expect(collapseButton).toHaveAttribute("aria-expanded", "true")
    })
  })
})
