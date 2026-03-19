// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * File Validation Utility
 *
 * Validate transcript file uploads (format and size constraints).
 */

export const ACCEPTED_FORMATS = [".vtt", ".json", ".txt"] as const
export type AcceptedFormat = (typeof ACCEPTED_FORMATS)[number]
export const MAX_FILE_SIZE = 10 * 1024 * 1024 // 10MB in bytes

function isAcceptedExtension(ext: string): ext is AcceptedFormat {
  return (ACCEPTED_FORMATS as readonly string[]).includes(ext)
}

export interface FileValidationResult {
  valid: boolean
  error?: string
}

/**
 * Validate transcript file format and size
 */
export function validateTranscriptFile(file: File): FileValidationResult {
  // Check if file exists
  if (!file) {
    return { valid: false, error: "No file provided" }
  }

  // Check file size
  if (file.size === 0) {
    return { valid: false, error: "File is empty" }
  }

  if (file.size > MAX_FILE_SIZE) {
    const sizeMB = (MAX_FILE_SIZE / (1024 * 1024)).toFixed(0)
    return { valid: false, error: `File size exceeds ${sizeMB}MB limit` }
  }

  // Check file format by extension
  const extension = getFileExtension(file.name)
  if (!isAcceptedExtension(extension)) {
    return {
      valid: false,
      error: `Invalid file format. Accepted formats: ${ACCEPTED_FORMATS.join(", ")}`,
    }
  }

  return { valid: true }
}

/**
 * Get file extension from filename (lowercase with dot)
 */
export function getFileExtension(filename: string): string {
  const lastDot = filename.lastIndexOf(".")
  if (lastDot === -1) return ""
  return filename.substring(lastDot).toLowerCase()
}

/**
 * Format file size for display
 */
export function formatFileSize(bytes: number): string {
  if (bytes === 0) return "0 Bytes"

  const k = 1024
  const sizes = ["Bytes", "KB", "MB", "GB"]
  const i = Math.floor(Math.log(bytes) / Math.log(k))

  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`
}

/**
 * Check if file format is accepted
 */
export function isAcceptedFormat(filename: string): boolean {
  const extension = getFileExtension(filename)
  return isAcceptedExtension(extension)
}
