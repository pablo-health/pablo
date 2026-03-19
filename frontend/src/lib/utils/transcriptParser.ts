// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Transcript Parser Utility
 *
 * Utilities for parsing and formatting transcript files in various formats (VTT, JSON, TXT).
 */

import type { TranscriptFormat, TranscriptModel } from "@/types/sessions"

/**
 * Detect transcript format from filename extension
 */
export function detectFormat(filename: string): TranscriptFormat {
  const ext = filename.split(".").pop()?.toLowerCase()
  if (ext === "vtt") return "vtt"
  if (ext === "json") return "json"
  return "txt"
}

/**
 * Parse transcript file and return TranscriptModel
 */
export function parseTranscriptFile(file: File): Promise<TranscriptModel> {
  return new Promise((resolve, reject) => {
    const format = detectFormat(file.name)
    const reader = new FileReader()

    reader.onload = (e) => {
      const content = e.target?.result as string
      if (!content) {
        reject(new Error("File is empty"))
        return
      }

      resolve({ format, content })
    }

    reader.onerror = () => reject(new Error("Failed to read file"))
    reader.readAsText(file)
  })
}

/**
 * Format transcript for display based on format
 */
export function formatTranscriptDisplay(transcript: TranscriptModel): string {
  if (transcript.format === "vtt") {
    return parseVTT(transcript.content)
  }
  if (transcript.format === "json") {
    return parseJSON(transcript.content)
  }
  return transcript.content
}

/**
 * Parse VTT format to readable transcript
 * Removes timestamps and "WEBVTT" header
 */
function parseVTT(content: string): string {
  const lines = content.split("\n")
  return lines
    .filter((line) => {
      // Remove WEBVTT header
      if (line.trim() === "WEBVTT" || line.trim().startsWith("WEBVTT ")) {
        return false
      }
      // Remove timestamp lines (format: HH:MM:SS.mmm --> HH:MM:SS.mmm)
      if (line.match(/^\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}/)) {
        return false
      }
      // Remove cue identifiers (standalone numbers)
      if (line.match(/^\d+$/)) {
        return false
      }
      // Keep non-empty lines
      return line.trim() !== ""
    })
    .join("\n")
}

/**
 * Parse JSON format to readable transcript
 * Handles array of objects with text/content fields
 */
function parseJSON(content: string): string {
  try {
    const data = JSON.parse(content)

    // Handle array of transcript entries
    if (Array.isArray(data)) {
      return data
        .map((item) => {
          // Try common field names
          return item.text || item.content || item.transcript || JSON.stringify(item)
        })
        .join("\n")
    }

    // Handle single object with text/content
    if (typeof data === "object" && data !== null) {
      return data.text || data.content || data.transcript || JSON.stringify(data, null, 2)
    }

    // Fallback to original content if structure is unrecognized
    return content
  } catch {
    // If JSON parsing fails, return as-is
    return content
  }
}
