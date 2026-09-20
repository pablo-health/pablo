// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One place to put one file, shared by both questions that ask for one.
 *
 * A card asks for two of these (front and back) and a document request for
 * as many as somebody has. What they share is the whole of the work — pick a
 * file, send it to storage, tell the form which question it answers, show it
 * back, take it off again — so it lives here once rather than twice.
 *
 * **Three calls, in an order that matters.** Ask where to put it, put it
 * there, then say what it answers. The last step is the only one that
 * touches the form, and the server writes the question's answer from the
 * rows rather than from anything sent here: a browser cannot attach a file
 * it does not own, because it never names one.
 *
 * **What a failure says.** The server writes the sentence for a refusal it
 * can explain — a file that is not the kind of file it claimed to be — and
 * anything else reads as "try again". Nothing here guesses at a cause.
 *
 * **Nothing here decides whether the question is answered.** The write
 * hands back the server's own progress, and the walk re-reads afterwards.
 */

import { useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import {
  attachArtifact,
  finishUpload,
  PatientIntakeError,
  removeArtifact,
  sendToStorage,
  startUpload,
  uploadPreviewUrl,
  type IntakeArtifact,
} from "@/lib/api/patientIntake"
import {
  UPLOAD_CHOOSE,
  UPLOAD_FAILED,
  UPLOAD_REMOVE,
  UPLOAD_SENDING,
  UPLOAD_SENT,
  UPLOAD_TAKE_PHOTO,
  UPLOAD_WRONG_TYPE,
} from "../formsCopy"

/** What the file picker offers, and what the server will accept. */
export const DEFAULT_ACCEPT = ["application/pdf", "image/jpeg", "image/png"]

export interface UploadSlotProps {
  assignmentId: string
  sessionToken: string
  itemId: string
  /** What to call this slot on the screen. Null for an unlabelled one. */
  label: string | null
  /** `"front"` / `"back"` on a card; absent on a document request. */
  side?: string
  /** What has already arrived for this slot, if anything. */
  artifact: IntakeArtifact | null
  /** The types this question takes. */
  accept: string[]
  /** True on a card, where a phone's camera is the obvious source. */
  camera?: boolean
  /** Raised after a write, so the walk re-reads the server's answer. */
  onWrote: () => void
  onSessionLost: () => void
}

export function UploadSlot({
  assignmentId,
  sessionToken,
  itemId,
  label,
  side,
  artifact,
  accept,
  camera = false,
  onWrote,
  onSessionLost,
}: UploadSlotProps) {
  const input = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<string | null>(null)

  function handle(raised: unknown) {
    if (raised instanceof PatientIntakeError && raised.kind === "expired") {
      onSessionLost()
      return
    }
    if (raised instanceof PatientIntakeError && raised.kind === "invalid") {
      // The server refused the file itself, and its sentence is the useful
      // one. A generic message here would be less useful and no safer.
      setError(raised.serverMessage ?? UPLOAD_WRONG_TYPE)
      return
    }
    setError(UPLOAD_FAILED)
  }

  async function send(file: File) {
    setBusy(true)
    setError(null)
    try {
      const started = await startUpload(sessionToken, file)
      await sendToStorage(started.upload, file)
      await finishUpload(sessionToken, started.document_id)
      await attachArtifact(sessionToken, assignmentId, {
        item_id: itemId,
        document_id: started.document_id,
        ...(side === undefined ? {} : { side }),
      })
      onWrote()
    } catch (raised: unknown) {
      handle(raised)
    } finally {
      setBusy(false)
      // Clear the picker so choosing the same file twice still fires.
      if (input.current) input.current.value = ""
    }
  }

  async function remove() {
    if (artifact === null) return
    setBusy(true)
    setError(null)
    try {
      await removeArtifact(sessionToken, assignmentId, artifact.id)
      setPreview(null)
      onWrote()
    } catch (raised: unknown) {
      handle(raised)
    } finally {
      setBusy(false)
    }
  }

  async function showPreview() {
    if (artifact === null || preview !== null) return
    try {
      setPreview(await uploadPreviewUrl(sessionToken, artifact.document_id))
    } catch (raised: unknown) {
      handle(raised)
    }
  }

  const testId = side === undefined ? "forms-upload" : `forms-upload-${side}`

  return (
    <div data-testid={testId} className="flex flex-col gap-2">
      {label !== null && <span className="text-sm font-medium text-neutral-800">{label}</span>}

      {artifact === null ? (
        <>
          <input
            ref={input}
            type="file"
            className="sr-only"
            data-testid={`${testId}-input`}
            accept={accept.join(",")}
            {...(camera ? { capture: "environment" as const } : {})}
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) void send(file)
            }}
          />
          <Button
            type="button"
            variant="outline"
            data-testid={`${testId}-choose`}
            disabled={busy}
            onClick={() => input.current?.click()}
          >
            {busy ? UPLOAD_SENDING : camera ? UPLOAD_TAKE_PHOTO : UPLOAD_CHOOSE}
          </Button>
        </>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <span
            data-testid={`${testId}-sent`}
            className="rounded-full bg-primary-50 px-2 py-0.5 text-xs font-medium text-primary-800"
          >
            {UPLOAD_SENT}
          </span>
          {preview === null ? (
            <button
              type="button"
              data-testid={`${testId}-preview`}
              className="text-sm text-primary-700 underline"
              onClick={() => void showPreview()}
            >
              View
            </button>
          ) : (
            // eslint-disable-next-line @next/next/no-img-element -- short-lived signed cross-origin URL; next/image can't optimize it, and proxying it would put a patient's file through the server
            <img
              data-testid={`${testId}-thumbnail`}
              src={preview}
              alt=""
              className="h-16 w-auto rounded border border-neutral-200 object-cover"
            />
          )}
          <button
            type="button"
            data-testid={`${testId}-remove`}
            className="text-sm text-neutral-600 underline"
            disabled={busy}
            onClick={() => void remove()}
          >
            {UPLOAD_REMOVE}
          </button>
        </div>
      )}

      {error !== null && (
        <p data-testid={`${testId}-error`} className="text-sm text-red-600">
          {error}
        </p>
      )}
    </div>
  )
}

/** The types a question takes, read defensively off its stored config. */
export function acceptOf(config: Record<string, unknown>): string[] {
  const accept = config.accept
  if (!Array.isArray(accept)) return DEFAULT_ACCEPT
  const named = accept.filter((kind): kind is string => typeof kind === "string")
  return named.length > 0 ? named : DEFAULT_ACCEPT
}
