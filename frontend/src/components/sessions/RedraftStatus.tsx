// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Where a session note's redraft stands. A redraft that fails leaves the
 * note exactly as it was (the backend marks it `failed` without touching
 * its content), which is what the failure line says.
 */

"use client"

import type { NoteGenerationStatus } from "@/types/notes"

export function RedraftStatus({
  status,
  requestFailed = false,
}: {
  status: NoteGenerationStatus
  /** The request to start a redraft was refused or didn't arrive. */
  requestFailed?: boolean
}) {
  if (status === "processing") {
    return (
      <p role="status" className="mb-3 rounded-md bg-neutral-50 px-3 py-2 text-sm text-neutral-700">
        Redrafting the note…
      </p>
    )
  }
  if (status === "failed" || requestFailed) {
    return (
      <p role="alert" className="mb-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
        The redraft didn&apos;t finish, so your note hasn&apos;t changed. Try again.
      </p>
    )
  }
  return null
}
