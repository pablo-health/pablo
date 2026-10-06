// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What sits under a note once it has been signed: the signature block, the
 * addenda with theirs, earlier signed versions, and the actions a locked note
 * allows — add an addendum, or unlock it to correct an error.
 *
 * A note finalized before signatures existed shows "Finalized <date>" and no
 * signature line; it stays locked and can be signed from here.
 */

"use client"

import { useState } from "react"
import { Lock, Plus, Unlock } from "lucide-react"
import { Button } from "@/components/ui/button"
import { NoteViewer } from "@/components/sessions/NoteViewer"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import {
  useAddNoteAddendum,
  useNoteSigning,
  useSignNote,
  useUnlockNote,
} from "@/hooks/useNoteSigning"
import { useUserTimeZone } from "@/hooks/usePreferences"
import {
  formatDay,
  formatSignedAt,
  signedByLine,
} from "@/lib/utils/signatureBlock"
import type { Note, NoteSignature } from "@/types/notes"
import { AddAddendumDialog, SignNoteDialog, UnlockNoteDialog } from "./SigningDialogs"

export interface NoteSignaturePanelProps {
  note: Note
  /**
   * Offer "Sign and lock" while the note is unlocked. Off where the page has
   * its own signing step (a session awaiting review, a new standalone note).
   */
  canSign?: boolean
  /** An addendum drafted from a dictation, offered for review and signing. */
  draftAddendum?: { dictationId: string; text: string }
}

function SignatureLines({
  name,
  credentials,
  at,
  timeZone,
}: {
  name: string
  credentials: string | null
  at: string
  timeZone: string
}) {
  return (
    <div className="text-sm text-neutral-800">
      <p>{signedByLine(name, credentials)}</p>
      <p className="text-neutral-600">{formatSignedAt(at, timeZone)}</p>
    </div>
  )
}

function VersionRow({
  note,
  version,
  timeZone,
}: {
  note: Note
  version: NoteSignature
  timeZone: string
}) {
  const [showing, setShowing] = useState(false)
  return (
    <li className="space-y-2 border-t border-neutral-200 pt-3 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-sm font-medium text-neutral-900">Version {version.version}</p>
          <SignatureLines
            name={version.signer_name}
            credentials={version.signer_credentials}
            at={version.signed_at}
            timeZone={timeZone}
          />
          {version.unlocked_at && (
            <p className="text-sm text-neutral-700">
              Unlocked {formatSignedAt(version.unlocked_at, timeZone)}: {version.unlock_reason}
            </p>
          )}
        </div>
        <Button variant="outline" size="sm" onClick={() => setShowing((s) => !s)}>
          {showing ? "Hide" : "View"}
        </Button>
      </div>
      {showing && (
        <NoteViewer
          readonly
          note={{
            ...note,
            note_type: version.note_type,
            note_type_version: version.note_type_version,
            content: version.content,
            content_edited: version.content_edited,
          }}
        />
      )}
    </li>
  )
}

export function NoteSignaturePanel({
  note,
  canSign = false,
  draftAddendum,
}: NoteSignaturePanelProps) {
  const { data: record } = useNoteSigning(note.id)
  const timeZone = useUserTimeZone()
  const { readOnly } = useReadOnlyMode()
  const sign = useSignNote()
  const unlock = useUnlockNote()
  const addAddendum = useAddNoteAddendum()
  const [dialog, setDialog] = useState<"sign" | "unlock" | "addendum" | "draft" | null>(null)

  if (!record) return null

  const locked = !!note.finalized_at
  const signature = record.signature
  const hasHistory = record.versions.some((v) => v.unlocked_at)
  const actions = readOnly
    ? []
    : locked
      ? [signature ? "unlock" : "sign", "addendum"]
      : canSign
        ? ["sign"]
        : []
  const draft = locked && !readOnly ? draftAddendum : undefined
  const nothingToShow =
    !locked && record.addenda.length === 0 && !hasHistory && actions.length === 0
  if (nothingToShow) return null

  const close = (open: boolean) => !open && setDialog(null)

  return (
    <section aria-label="Signature" className="card space-y-4">
      {signature ? (
        <div data-testid="signature-block" className="flex items-start gap-2">
          <Lock className="mt-0.5 h-4 w-4 text-neutral-500" aria-hidden />
          <SignatureLines
            name={signature.signer_name}
            credentials={signature.signer_credentials}
            at={signature.signed_at}
            timeZone={timeZone}
          />
        </div>
      ) : locked && note.finalized_at ? (
        <p data-testid="signature-block" className="text-sm text-neutral-700">
          Finalized {formatDay(note.finalized_at, timeZone)}
        </p>
      ) : null}

      {record.addenda.length > 0 && (
        <div className="space-y-3">
          <h4 className="text-sm font-semibold text-neutral-900">Addenda</h4>
          <ul className="space-y-3">
            {record.addenda.map((addendum) => (
              <li key={addendum.id} className="rounded-md border border-neutral-200 p-3">
                <p className="whitespace-pre-wrap text-sm text-neutral-900">{addendum.text}</p>
                <div className="mt-2">
                  <SignatureLines
                    name={addendum.signer_name}
                    credentials={addendum.signer_credentials}
                    at={addendum.created_at}
                    timeZone={timeZone}
                  />
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {draft && (
        <div
          data-testid="draft-addendum"
          className="space-y-2 rounded-md border border-dashed border-neutral-300 p-3"
        >
          <h4 className="text-sm font-semibold text-neutral-900">
            Draft addendum from your dictation
          </h4>
          <p className="whitespace-pre-wrap text-sm text-neutral-800">{draft.text}</p>
          <div className="flex justify-end">
            <Button size="sm" onClick={() => setDialog("draft")}>
              Review and sign
            </Button>
          </div>
        </div>
      )}

      {hasHistory && (
        <details className="text-sm">
          <summary className="cursor-pointer text-neutral-600">
            Signed versions ({record.versions.length})
          </summary>
          <ul className="mt-3 space-y-3">
            {record.versions.map((version) => (
              <VersionRow key={version.id} note={note} version={version} timeZone={timeZone} />
            ))}
          </ul>
        </details>
      )}

      {actions.length > 0 && (
        <div className="flex flex-wrap justify-end gap-2">
          {actions.includes("addendum") && (
            <Button variant="outline" onClick={() => setDialog("addendum")}>
              <Plus className="mr-2 h-4 w-4" />
              Add addendum
            </Button>
          )}
          {actions.includes("unlock") && (
            <Button variant="outline" onClick={() => setDialog("unlock")}>
              <Unlock className="mr-2 h-4 w-4" />
              Unlock
            </Button>
          )}
          {actions.includes("sign") && (
            <Button onClick={() => setDialog("sign")}>
              <Lock className="mr-2 h-4 w-4" />
              {locked ? "Sign note" : "Sign and lock"}
            </Button>
          )}
        </div>
      )}

      <SignNoteDialog
        open={dialog === "sign"}
        onOpenChange={close}
        onSign={(signer) => sign.mutateAsync({ noteId: note.id, data: signer })}
      />
      <AddAddendumDialog
        open={dialog === "addendum"}
        onOpenChange={close}
        onAdd={(text, signer) =>
          addAddendum.mutateAsync({ noteId: note.id, data: { text, ...signer } })
        }
      />
      {draft && (
        <AddAddendumDialog
          key={draft.dictationId}
          open={dialog === "draft"}
          onOpenChange={close}
          initialText={draft.text}
          onAdd={(text, signer) =>
            addAddendum.mutateAsync({
              noteId: note.id,
              data: { text, ...signer, dictation_id: draft.dictationId },
            })
          }
        />
      )}
      <UnlockNoteDialog
        open={dialog === "unlock"}
        onOpenChange={close}
        onUnlock={(reason) => unlock.mutateAsync({ noteId: note.id, data: { reason } })}
      />
    </section>
  )
}
