// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * TranscriptParser Utility Tests
 *
 * Comprehensive tests for transcript parsing and formatting utilities.
 */

import { describe, it, expect } from "vitest"
import {
  detectFormat,
  formatTranscriptDisplay,
  parseTranscriptFile,
} from "../transcriptParser"
import type { TranscriptModel } from "@/types/sessions"

describe("transcriptParser", () => {
  describe("detectFormat", () => {
    const formatTests = [
      { filename: "transcript.vtt", expected: "vtt" },
      { filename: "transcript.json", expected: "json" },
      { filename: "transcript.txt", expected: "txt" },
      { filename: "TRANSCRIPT.VTT", expected: "vtt" },
      { filename: "Transcript.JSON", expected: "json" },
      { filename: "transcript.TXT", expected: "txt" },
      { filename: "my.transcript.file.vtt", expected: "vtt" },
      { filename: "session.2024.01.15.json", expected: "json" },
    ]

    formatTests.forEach(({ filename, expected }) => {
      it(`detects ${expected} format from ${filename}`, () => {
        expect(detectFormat(filename)).toBe(expected)
      })
    })

    it("defaults to TXT for unknown extensions", () => {
      expect(detectFormat("transcript.doc")).toBe("txt")
      expect(detectFormat("transcript.pdf")).toBe("txt")
      expect(detectFormat("transcript")).toBe("txt")
    })
  })

  describe("formatTranscriptDisplay", () => {
    describe("VTT format", () => {
      it("removes WEBVTT header", () => {
        const transcript: TranscriptModel = {
          format: "vtt",
          content: "WEBVTT\n\nSpeaker: Hello world",
        }

        const result = formatTranscriptDisplay(transcript)
        expect(result).not.toContain("WEBVTT")
        expect(result).toContain("Speaker: Hello world")
      })

      it("removes WEBVTT header with metadata", () => {
        const transcript: TranscriptModel = {
          format: "vtt",
          content: "WEBVTT - This is metadata\n\nSpeaker: Hello",
        }

        const result = formatTranscriptDisplay(transcript)
        expect(result).not.toContain("WEBVTT")
      })

      it("removes timestamp lines", () => {
        const transcript: TranscriptModel = {
          format: "vtt",
          content: `WEBVTT

00:00:00.000 --> 00:00:05.000
Hello, this is the first line

00:00:05.001 --> 00:00:10.000
And this is the second line`,
        }

        const result = formatTranscriptDisplay(transcript)
        expect(result).not.toContain("00:00:00.000")
        expect(result).not.toContain("-->")
        expect(result).toContain("Hello, this is the first line")
        expect(result).toContain("And this is the second line")
      })

      it("removes cue identifiers", () => {
        const transcript: TranscriptModel = {
          format: "vtt",
          content: `WEBVTT

1
00:00:00.000 --> 00:00:05.000
First line

2
00:00:05.001 --> 00:00:10.000
Second line`,
        }

        const result = formatTranscriptDisplay(transcript)
        expect(result).not.toMatch(/^1$/m)
        expect(result).not.toMatch(/^2$/m)
        expect(result).toContain("First line")
        expect(result).toContain("Second line")
      })

      it("removes empty lines", () => {
        const transcript: TranscriptModel = {
          format: "vtt",
          content: `WEBVTT

1
00:00:00.000 --> 00:00:05.000
Text line


Another text line`,
        }

        const result = formatTranscriptDisplay(transcript)
        const lines = result.split("\n")
        expect(lines.every((line) => line.trim() !== "")).toBe(true)
      })

      it("preserves actual transcript text", () => {
        const transcript: TranscriptModel = {
          format: "vtt",
          content: `WEBVTT

00:00:00.000 --> 00:00:05.000
Therapist: How are you feeling today?

00:00:05.001 --> 00:00:10.000
Patient: I've been feeling much better, thank you.`,
        }

        const result = formatTranscriptDisplay(transcript)
        expect(result).toContain("Therapist: How are you feeling today?")
        expect(result).toContain("Patient: I've been feeling much better, thank you.")
      })
    })

    describe("JSON format", () => {
      it("extracts text from array of objects with text field", () => {
        const transcript: TranscriptModel = {
          format: "json",
          content: JSON.stringify([
            { text: "First line", timestamp: 0 },
            { text: "Second line", timestamp: 5 },
          ]),
        }

        const result = formatTranscriptDisplay(transcript)
        expect(result).toContain("First line")
        expect(result).toContain("Second line")
        expect(result).not.toContain("timestamp")
      })

      it("extracts content from array of objects with content field", () => {
        const transcript: TranscriptModel = {
          format: "json",
          content: JSON.stringify([
            { content: "Line one" },
            { content: "Line two" },
          ]),
        }

        const result = formatTranscriptDisplay(transcript)
        expect(result).toContain("Line one")
        expect(result).toContain("Line two")
      })

      it("extracts transcript field from array", () => {
        const transcript: TranscriptModel = {
          format: "json",
          content: JSON.stringify([
            { transcript: "First" },
            { transcript: "Second" },
          ]),
        }

        const result = formatTranscriptDisplay(transcript)
        expect(result).toContain("First")
        expect(result).toContain("Second")
      })

      it("handles single object with text field", () => {
        const transcript: TranscriptModel = {
          format: "json",
          content: JSON.stringify({
            text: "This is the full transcript",
          }),
        }

        const result = formatTranscriptDisplay(transcript)
        expect(result).toBe("This is the full transcript")
      })

      it("handles single object with content field", () => {
        const transcript: TranscriptModel = {
          format: "json",
          content: JSON.stringify({
            content: "Transcript content here",
          }),
        }

        const result = formatTranscriptDisplay(transcript)
        expect(result).toBe("Transcript content here")
      })

      it("falls back to stringified JSON for unrecognized structure", () => {
        const transcript: TranscriptModel = {
          format: "json",
          content: JSON.stringify({
            unknown_field: "data",
            another_field: "more data",
          }),
        }

        const result = formatTranscriptDisplay(transcript)
        expect(result).toContain("unknown_field")
        expect(result).toContain("data")
      })

      it("returns original content if JSON parsing fails", () => {
        const transcript: TranscriptModel = {
          format: "json",
          content: "This is not valid JSON {{{",
        }

        const result = formatTranscriptDisplay(transcript)
        expect(result).toBe("This is not valid JSON {{{")
      })
    })

    describe("TXT format", () => {
      it("returns content as-is", () => {
        const transcript: TranscriptModel = {
          format: "txt",
          content: "This is plain text\nWith multiple lines\nAnd no formatting",
        }

        const result = formatTranscriptDisplay(transcript)
        expect(result).toBe(transcript.content)
      })

      it("preserves whitespace and formatting", () => {
        const transcript: TranscriptModel = {
          format: "txt",
          content: "Line 1\n\n  Indented line\n\nLine 3",
        }

        const result = formatTranscriptDisplay(transcript)
        expect(result).toBe(transcript.content)
      })
    })
  })

  describe("parseTranscriptFile", () => {
    const parseTests = [
      { format: "vtt", filename: "transcript.vtt", type: "text/vtt", content: "WEBVTT\n\nSpeaker: Hello" },
      { format: "json", filename: "transcript.json", type: "application/json", content: JSON.stringify({ text: "Hello world" }) },
      { format: "txt", filename: "transcript.txt", type: "text/plain", content: "Plain text transcript" },
    ]

    parseTests.forEach(({ format, filename, type, content }) => {
      it(`parses ${format.toUpperCase()} file correctly`, async () => {
        const file = new File([content], filename, { type })
        const result = await parseTranscriptFile(file)

        expect(result.format).toBe(format)
        expect(result.content).toBe(content)
      })
    })

    it("rejects empty files", async () => {
      const file = new File([""], "empty.vtt", { type: "text/vtt" })

      await expect(parseTranscriptFile(file)).rejects.toThrow("File is empty")
    })

    it("detects format from filename regardless of MIME type", async () => {
      const content = "VTT content"
      const file = new File([content], "transcript.vtt", {
        type: "application/octet-stream",
      })

      const result = await parseTranscriptFile(file)

      expect(result.format).toBe("vtt")
    })

    it("handles files with uppercase extensions", async () => {
      const content = "Content"
      const file = new File([content], "TRANSCRIPT.JSON", {
        type: "application/json",
      })

      const result = await parseTranscriptFile(file)

      expect(result.format).toBe("json")
    })

    it("preserves original content including special characters", async () => {
      const content = "Special: ñ, ü, é, 中文, 🎉"
      const file = new File([content], "transcript.txt", { type: "text/plain" })

      const result = await parseTranscriptFile(file)

      expect(result.content).toBe(content)
    })

    it("handles large files", async () => {
      const lines = Array(10000).fill("Line").join("\n") // 10,000 lines
      const file = new File([lines], "large.txt", { type: "text/plain" })

      const result = await parseTranscriptFile(file)

      expect(result.content).toBe(lines)
      expect(result.content.split("\n")).toHaveLength(10000)
    })
  })

  describe("Edge Cases", () => {
    it("handles VTT with no actual content", () => {
      const transcript: TranscriptModel = {
        format: "vtt",
        content: "WEBVTT\n\n",
      }

      const result = formatTranscriptDisplay(transcript)
      expect(result).toBe("")
    })

    it("handles JSON array with mixed field names", () => {
      const transcript: TranscriptModel = {
        format: "json",
        content: JSON.stringify([
          { text: "First" },
          { content: "Second" },
          { transcript: "Third" },
        ]),
      }

      const result = formatTranscriptDisplay(transcript)
      expect(result).toContain("First")
      expect(result).toContain("Second")
      expect(result).toContain("Third")
    })

    it("handles JSON array with objects missing expected fields", () => {
      const transcript: TranscriptModel = {
        format: "json",
        content: JSON.stringify([{ id: 1 }, { id: 2 }]),
      }

      const result = formatTranscriptDisplay(transcript)
      // Should fall back to stringifying each object
      expect(result).toContain("id")
    })

    it("handles malformed VTT timestamps", () => {
      const transcript: TranscriptModel = {
        format: "vtt",
        content: `WEBVTT

invalid timestamp
This is text

00:00:05.001 -> 00:00:10.000
More text`,
      }

      const result = formatTranscriptDisplay(transcript)
      expect(result).toContain("invalid timestamp")
      expect(result).toContain("This is text")
      expect(result).toContain("More text")
    })

    it("handles empty string content", () => {
      const transcript: TranscriptModel = {
        format: "txt",
        content: "",
      }

      const result = formatTranscriptDisplay(transcript)
      expect(result).toBe("")
    })
  })
})
