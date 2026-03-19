// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * File Validation Utility Tests
 *
 * Tests for transcript file upload validation (format and size constraints).
 */

import { describe, it, expect } from "vitest"
import {
  validateTranscriptFile,
  getFileExtension,
  formatFileSize,
  isAcceptedFormat,
  ACCEPTED_FORMATS,
  MAX_FILE_SIZE,
} from "../fileValidation"

describe("fileValidation", () => {
  describe("validateTranscriptFile", () => {
    const validFormats = [
      { ext: ".vtt", filename: "transcript.vtt", type: "text/vtt", content: "test content" },
      { ext: ".json", filename: "transcript.json", type: "application/json", content: '{"text": "test"}' },
      { ext: ".txt", filename: "transcript.txt", type: "text/plain", content: "test content" },
    ]

    validFormats.forEach(({ ext, filename, type, content }) => {
      it(`accepts valid ${ext} file`, () => {
        const file = new File([content], filename, { type })
        const result = validateTranscriptFile(file)

        expect(result.valid).toBe(true)
        expect(result.error).toBeUndefined()
      })
    })

    it("accepts uppercase and mixed case file extensions", () => {
      const file = new File(["test content"], "TRANSCRIPT.VTT", {
        type: "text/vtt",
      })

      const result = validateTranscriptFile(file)

      expect(result.valid).toBe(true)
    })

    const invalidFormats = [
      { filename: "transcript.pdf", type: "application/pdf" },
      { filename: "transcript.doc", type: "application/msword" },
      { filename: "transcript.docx", type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" },
      { filename: "transcript.csv", type: "text/csv" },
    ]

    invalidFormats.forEach(({ filename, type }) => {
      it(`rejects ${filename.split('.').pop()} file`, () => {
        const file = new File(["test content"], filename, { type })
        const result = validateTranscriptFile(file)

        expect(result.valid).toBe(false)
        expect(result.error).toContain("Invalid file format")
      })
    })

    it("rejects file without extension", () => {
      const file = new File(["test content"], "transcript", {
        type: "text/plain",
      })

      const result = validateTranscriptFile(file)

      expect(result.valid).toBe(false)
      expect(result.error).toContain("Invalid file format")
    })

    it("rejects empty file (0 bytes)", () => {
      const file = new File([], "transcript.vtt", { type: "text/vtt" })

      const result = validateTranscriptFile(file)

      expect(result.valid).toBe(false)
      expect(result.error).toBe("File is empty")
    })

    it("accepts file just under size limit (10MB - 1 byte)", () => {
      const content = new Array(MAX_FILE_SIZE - 1).fill("a").join("")
      const file = new File([content], "transcript.vtt", {
        type: "text/vtt",
      })

      const result = validateTranscriptFile(file)

      expect(result.valid).toBe(true)
    })

    it("accepts file at exact size limit (10MB)", () => {
      const content = new Array(MAX_FILE_SIZE).fill("a").join("")
      const file = new File([content], "transcript.vtt", {
        type: "text/vtt",
      })

      const result = validateTranscriptFile(file)

      expect(result.valid).toBe(true)
    })

    it("rejects file exceeding size limit (10MB + 1 byte)", () => {
      const largeContent = new Array(MAX_FILE_SIZE + 1).fill("a").join("")
      const file = new File([largeContent], "transcript.vtt", {
        type: "text/vtt",
      })

      const result = validateTranscriptFile(file)

      expect(result.valid).toBe(false)
      expect(result.error).toContain("File size exceeds")
      expect(result.error).toContain("10MB")
    })

    it("handles file with special characters in name", () => {
      const file = new File(
        ["test content"],
        "transcript (1) - copy [final].vtt",
        {
          type: "text/vtt",
        }
      )

      const result = validateTranscriptFile(file)

      expect(result.valid).toBe(true)
    })

    it("handles file with multiple dots in name", () => {
      const file = new File(
        ["test content"],
        "transcript.backup.final.vtt",
        {
          type: "text/vtt",
        }
      )

      const result = validateTranscriptFile(file)

      expect(result.valid).toBe(true)
    })

    it("rejects null file", () => {
      const result = validateTranscriptFile(null as any)

      expect(result.valid).toBe(false)
      expect(result.error).toBe("No file provided")
    })

    it("rejects undefined file", () => {
      const result = validateTranscriptFile(undefined as any)

      expect(result.valid).toBe(false)
      expect(result.error).toBe("No file provided")
    })

  })

  describe("getFileExtension", () => {
    const extensionTests = [
      { filename: "transcript.vtt", expected: ".vtt" },
      { filename: "transcript.json", expected: ".json" },
      { filename: "transcript.txt", expected: ".txt" },
      { filename: "TRANSCRIPT.VTT", expected: ".vtt" },
      { filename: "transcript.JSON", expected: ".json" },
      { filename: "Transcript.TxT", expected: ".txt" },
    ]

    extensionTests.forEach(({ filename, expected }) => {
      it(`extracts and lowercases extension from ${filename}`, () => {
        expect(getFileExtension(filename)).toBe(expected)
      })
    })

    it("handles multiple dots in filename", () => {
      expect(getFileExtension("transcript.backup.vtt")).toBe(".vtt")
      expect(getFileExtension("my.file.name.json")).toBe(".json")
    })

    it("returns empty string for no extension", () => {
      expect(getFileExtension("transcript")).toBe("")
      expect(getFileExtension("noextension")).toBe("")
    })

    it("handles filename ending with dot", () => {
      expect(getFileExtension("transcript.")).toBe(".")
    })

    it("handles empty filename", () => {
      expect(getFileExtension("")).toBe("")
    })

    it("handles hidden files", () => {
      expect(getFileExtension(".gitignore")).toBe(".gitignore")
      expect(getFileExtension(".env.local")).toBe(".local")
    })
  })

  describe("formatFileSize", () => {
    const formatTests = [
      { bytes: 0, expected: "0 Bytes" },
      { bytes: 100, expected: "100 Bytes" },
      { bytes: 1023, expected: "1023 Bytes" },
      { bytes: 1024, expected: "1 KB" },
      { bytes: 2048, expected: "2 KB" },
      { bytes: 1536, expected: "1.5 KB" },
      { bytes: 1024 * 1024, expected: "1 MB" },
      { bytes: 2 * 1024 * 1024, expected: "2 MB" },
      { bytes: 1.5 * 1024 * 1024, expected: "1.5 MB" },
      { bytes: 1024 * 1024 * 1024, expected: "1 GB" },
      { bytes: 2.5 * 1024 * 1024 * 1024, expected: "2.5 GB" },
    ]

    formatTests.forEach(({ bytes, expected }) => {
      it(`formats ${bytes} bytes as "${expected}"`, () => {
        expect(formatFileSize(bytes)).toBe(expected)
      })
    })

    it("rounds to 2 decimal places", () => {
      expect(formatFileSize(1234)).toBe("1.21 KB")
      expect(formatFileSize(1234567)).toBe("1.18 MB")
    })
  })

  describe("isAcceptedFormat", () => {
    const acceptedFiles = [
      "transcript.vtt",
      "transcript.json",
      "transcript.txt",
      "TRANSCRIPT.VTT",
      "transcript.JSON",
      "Transcript.TxT",
    ]

    acceptedFiles.forEach((filename) => {
      it(`returns true for ${filename}`, () => {
        expect(isAcceptedFormat(filename)).toBe(true)
      })
    })

    const rejectedFiles = [
      "transcript.pdf",
      "transcript.doc",
      "transcript.csv",
    ]

    rejectedFiles.forEach((filename) => {
      it(`returns false for ${filename}`, () => {
        expect(isAcceptedFormat(filename)).toBe(false)
      })
    })

    it("returns false for no extension", () => {
      expect(isAcceptedFormat("transcript")).toBe(false)
    })

    it("handles multiple dots in filename", () => {
      expect(isAcceptedFormat("transcript.backup.vtt")).toBe(true)
      expect(isAcceptedFormat("file.name.pdf")).toBe(false)
    })
  })

  describe("Constants", () => {
    it("ACCEPTED_FORMATS includes all required formats", () => {
      expect(ACCEPTED_FORMATS).toContain(".vtt")
      expect(ACCEPTED_FORMATS).toContain(".json")
      expect(ACCEPTED_FORMATS).toContain(".txt")
      expect(ACCEPTED_FORMATS).toHaveLength(3)
    })

    it("MAX_FILE_SIZE is 10MB", () => {
      expect(MAX_FILE_SIZE).toBe(10 * 1024 * 1024)
    })
  })
})
