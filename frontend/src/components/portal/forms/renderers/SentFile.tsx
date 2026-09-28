// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One file slot, drawn read-only: what arrived, and a way to open it.
 *
 * The read-only half of `UploadSlot`. It holds no picker and reaches no
 * patient route; the file is named and opened through the `ReadOnlySource`
 * the caller handed in, so whoever draws the form decides how a file is
 * opened and what that read records.
 */

import { useState } from "react"
import type { IntakeArtifact } from "@/lib/api/patientIntake"
import { UPLOAD_FAILED_OPEN, UPLOAD_NOT_SENT, UPLOAD_SENT, UPLOAD_VIEW } from "../formsCopy"
import type { ReadOnlySource } from "./types"

export function SentFile({
  label,
  side,
  artifact,
  readOnly,
}: {
  label: string | null
  side?: string
  artifact: IntakeArtifact | null
  readOnly: ReadOnlySource
}) {
  const testId = side === undefined ? "forms-upload" : `forms-upload-${side}`
  const [failed, setFailed] = useState(false)

  async function open(documentId: string) {
    setFailed(false)
    try {
      await readOnly.openFile(documentId)
    } catch {
      setFailed(true)
    }
  }

  return (
    <div data-testid={testId} className="flex flex-col gap-2">
      {label !== null && <span className="text-sm font-medium text-neutral-800">{label}</span>}
      {artifact === null ? (
        <p className="text-sm text-neutral-600">{UPLOAD_NOT_SENT}</p>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <span
            data-testid={`${testId}-sent`}
            className="rounded-full bg-primary-50 px-2 py-0.5 text-xs font-medium text-primary-800"
          >
            {UPLOAD_SENT}
          </span>
          <span data-testid={`${testId}-filename`} className="min-w-0 truncate text-sm text-neutral-900">
            {readOnly.filenames[artifact.id] ?? ""}
          </span>
          <button
            type="button"
            data-testid={`${testId}-view`}
            className="text-sm text-primary-700 underline print:hidden"
            onClick={() => void open(artifact.document_id)}
          >
            {UPLOAD_VIEW}
          </button>
        </div>
      )}
      {failed && (
        <p data-testid={`${testId}-error`} className="text-sm text-red-600">
          {UPLOAD_FAILED_OPEN}
        </p>
      )}
    </div>
  )
}
