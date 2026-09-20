// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Write into a thread that already exists.
 *
 * Send is blocked while a send is in flight. There is no idempotency key
 * on the route behind it, so a second click would be a second message
 * rather than a retry of the first.
 *
 * The draft is cleared only once the send has resolved. A rejection
 * leaves the text where the patient can see it and try again, and the
 * rejection itself is swallowed here because the caller owns the error
 * copy — nothing about the message is logged or thrown onward.
 *
 * Files go up one at a time, as soon as they are picked, and are held as
 * chips until the message is sent. That ordering is what the route needs:
 * a send names documents that already exist, so the upload cannot wait for
 * the send. A failed upload is kept as a retry rather than dropped — the
 * patient picked that file for a reason — and it never blocks sending the
 * words on their own, which is the state somebody gives up in.
 */

"use client"

import { useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { ALLOWED_DOCUMENT_MIME_TYPES } from "@/types/patientDocuments"
import { ExpectationNotice } from "./ExpectationNotice"

/** One file already uploaded and waiting to be sent. */
export interface ComposerAttachment {
  documentId: string
  filename: string
  sizeBytes: number
}

/** The server refuses a sixth; the button stops offering one. */
export const MAX_ATTACHMENTS = 5

const UPLOAD_FAILED = "That file didn't upload."

export interface MessageComposerProps {
  onSend: (body: string, attachmentIds: string[]) => Promise<unknown>
  sending: boolean
  slaText?: string | null
  error?: string | null
  /**
   * Upload one file and return the document it became. Leave it out and
   * the composer offers no attach button at all, which is what a surface
   * with nowhere to put a file should do.
   */
  onAttach?: (file: File) => Promise<ComposerAttachment>
}

/** Round sizes the way a file manager does; the exact byte count helps nobody. */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function MessageComposer({
  onSend,
  sending,
  slaText,
  error,
  onAttach,
}: MessageComposerProps) {
  const [body, setBody] = useState("")
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([])
  const [uploading, setUploading] = useState(false)
  const [failed, setFailed] = useState<File | null>(null)
  const fileInput = useRef<HTMLInputElement | null>(null)

  const canSend = body.trim().length > 0 && !sending && !uploading
  const canAttach =
    onAttach !== undefined &&
    !uploading &&
    !sending &&
    attachments.length < MAX_ATTACHMENTS

  async function upload(file: File) {
    if (!onAttach) return
    setUploading(true)
    setFailed(null)
    try {
      const uploaded = await onAttach(file)
      setAttachments((current) => [...current, uploaded])
    } catch {
      // Held rather than reported onward: the caller's error copy is about
      // the message, and this one is about a file the patient can retry.
      setFailed(file)
    } finally {
      setUploading(false)
    }
  }

  function handlePicked(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    // Cleared so picking the same file again still fires a change event.
    event.target.value = ""
    if (file) void upload(file)
  }

  function remove(documentId: string) {
    setAttachments((current) => current.filter((a) => a.documentId !== documentId))
  }

  async function handleSend() {
    if (!canSend) return
    try {
      await onSend(
        body.trim(),
        attachments.map((a) => a.documentId),
      )
      setBody("")
      setAttachments([])
      setFailed(null)
    } catch {
      // The caller renders the failure. Keeping the draft is the point.
    }
  }

  return (
    <div className="flex flex-col gap-3" data-testid="portal-messaging-composer">
      <ExpectationNotice slaText={slaText} />
      <div>
        <Label htmlFor="portal-message-body">Your message</Label>
        <Textarea
          id="portal-message-body"
          data-testid="portal-messaging-composer-body"
          value={body}
          onChange={(event) => setBody(event.target.value)}
          disabled={sending}
          rows={4}
          className="mt-1"
        />
      </div>

      {onAttach && (
        <div className="flex flex-col gap-2">
          {attachments.length > 0 && (
            <ul
              className="flex flex-wrap gap-2"
              data-testid="portal-messaging-composer-attachments"
            >
              {attachments.map((attachment) => (
                <li
                  key={attachment.documentId}
                  data-testid={`portal-messaging-composer-chip-${attachment.documentId}`}
                  className="flex items-center gap-2 rounded-full bg-neutral-100 px-3 py-1 text-sm text-neutral-900"
                >
                  <span>{attachment.filename}</span>
                  <span className="text-xs text-neutral-500">
                    {formatFileSize(attachment.sizeBytes)}
                  </span>
                  <button
                    type="button"
                    aria-label={`Remove ${attachment.filename}`}
                    data-testid={`portal-messaging-composer-remove-${attachment.documentId}`}
                    className="text-neutral-500"
                    onClick={() => remove(attachment.documentId)}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}

          {failed && (
            <div
              className="flex items-center gap-3"
              data-testid="portal-messaging-composer-attach-error"
            >
              <p className="text-sm text-red-600">{UPLOAD_FAILED}</p>
              <Button
                variant="ghost"
                data-testid="portal-messaging-composer-attach-retry"
                onClick={() => void upload(failed)}
                disabled={uploading}
              >
                Try again
              </Button>
            </div>
          )}

          <input
            ref={fileInput}
            type="file"
            className="hidden"
            data-testid="portal-messaging-composer-file"
            accept={ALLOWED_DOCUMENT_MIME_TYPES.join(",")}
            onChange={handlePicked}
          />
          <Button
            variant="ghost"
            className="self-start"
            data-testid="portal-messaging-composer-attach"
            onClick={() => fileInput.current?.click()}
            disabled={!canAttach}
          >
            {uploading ? "Adding…" : "Attach a file"}
          </Button>
        </div>
      )}

      {error && (
        <p data-testid="portal-messaging-composer-error" className="text-sm text-red-600">
          {error}
        </p>
      )}
      <Button
        data-testid="portal-messaging-composer-send"
        onClick={handleSend}
        disabled={!canSend}
      >
        {sending ? "Sending…" : "Send"}
      </Button>
    </div>
  )
}
