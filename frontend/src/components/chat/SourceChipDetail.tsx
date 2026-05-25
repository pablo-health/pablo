// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Per-source detail dialog (§13.2). Opens when the chip's caret is
 * clicked. Renders the latest manifest entry for that source (token
 * estimate, row count, contributing note ids as new-tab links, drop
 * reason if any), and offers "Set as default" to persist the toggled
 * selection on ``default_source_selection``.
 *
 * Built on shadcn ``Dialog`` (already vendored) rather than a Popover
 * to avoid adding a new Radix dep for v1; the modal feel is fine for
 * a deep-detail surface that's rarely opened mid-thread.
 */

import { ExternalLink } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import type {
  ContextManifest,
  ManifestDroppedEntry,
  ManifestIncludedEntry,
  SourceKey,
  SourceParams,
} from "@/lib/chat/types"
import { SOURCE_META } from "@/lib/chat/sourceMeta"

import { SourceParamsEditor } from "./SourceParamsEditor"

const EDITABLE_SOURCES: ReadonlySet<SourceKey> = new Set<SourceKey>([
  "pasted_text",
  "patient_documents",
])

interface SourceChipDetailProps {
  open: boolean
  sourceKey: SourceKey | null
  /** Patient whose documents the patient_documents picker lists. */
  patientId: string
  /** Current per-turn params for this source, used to seed the editor. */
  selectionValue?: SourceParams
  /** Most recent manifest, used to pull this source's forensic entry. */
  manifest: ContextManifest | null
  /**
   * Whether this source is currently part of the conversation's
   * ``default_source_selection`` — drives the "Set as default" copy.
   */
  isDefault: boolean
  onOpenChange: (open: boolean) => void
  /** Persist shaped params (pasted_text content, picked document_ids). */
  onApplyParams: (key: SourceKey, params: SourceParams) => void
  onSetAsDefault: (key: SourceKey) => void
  onOpenNote?: (noteId: string) => void
}

export function SourceChipDetail({
  open,
  sourceKey,
  patientId,
  selectionValue,
  manifest,
  isDefault,
  onOpenChange,
  onApplyParams,
  onSetAsDefault,
  onOpenNote,
}: SourceChipDetailProps) {
  if (!sourceKey) return null

  const meta = SOURCE_META[sourceKey]
  const isEditable = EDITABLE_SOURCES.has(sourceKey)
  const includedEntry: ManifestIncludedEntry | undefined = manifest?.sources_included.find(
    (e) => e.source_key === sourceKey,
  )
  const droppedEntry: ManifestDroppedEntry | undefined = manifest?.sources_dropped.find(
    (e) => e.source_key === sourceKey,
  )

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-slot="chat-source-detail" className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <meta.icon className="size-4 text-neutral-600" />
            {meta.label}
          </DialogTitle>
          <DialogDescription>{meta.description}</DialogDescription>
        </DialogHeader>

        {isEditable ? (
          <SourceParamsEditor
            key={sourceKey}
            sourceKey={sourceKey}
            patientId={patientId}
            value={selectionValue}
            onApply={(params) => {
              onApplyParams(sourceKey, params)
              onOpenChange(false)
            }}
          />
        ) : includedEntry ? (
          <IncludedDetail entry={includedEntry} onOpenNote={onOpenNote} />
        ) : droppedEntry ? (
          <DroppedDetail entry={droppedEntry} />
        ) : (
          <p className="text-sm text-neutral-600">
            This source hasn&apos;t been used in this conversation yet. Send a
            message to see what lands in the model&apos;s context.
          </p>
        )}

        <DialogFooter className="sm:justify-between">
          <Button
            type="button"
            variant="ghost"
            onClick={() => onSetAsDefault(sourceKey)}
            disabled={isDefault}
          >
            {isDefault ? "Already your default" : "Set as default for this conversation"}
          </Button>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function IncludedDetail({
  entry,
  onOpenNote,
}: {
  entry: ManifestIncludedEntry
  onOpenNote?: (noteId: string) => void
}) {
  return (
    <div className="space-y-3 text-sm text-neutral-700">
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-neutral-500">Tokens</dt>
        <dd>~{entry.tokens_est.toLocaleString()}</dd>
        {typeof entry.row_count === "number" ? (
          <>
            <dt className="text-neutral-500">Rows included</dt>
            <dd>{entry.row_count}</dd>
          </>
        ) : null}
        {typeof entry.chars === "number" ? (
          <>
            <dt className="text-neutral-500">Characters</dt>
            <dd>{entry.chars.toLocaleString()}</dd>
          </>
        ) : null}
        {entry.rows_dropped && entry.rows_dropped > 0 ? (
          <>
            <dt className="text-neutral-500">Dropped to fit</dt>
            <dd>{entry.rows_dropped}</dd>
          </>
        ) : null}
      </dl>

      {entry.note_ids && entry.note_ids.length > 0 ? (
        <div>
          <p className="text-xs font-medium text-neutral-600 mb-1">Notes used</p>
          <ul className="space-y-1">
            {entry.note_ids.map((noteId) => (
              <li key={noteId}>
                <NoteOpenLink noteId={noteId} onOpenNote={onOpenNote} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {entry.dropped_note_ids && entry.dropped_note_ids.length > 0 ? (
        <div>
          <p className="text-xs font-medium text-neutral-600 mb-1">
            Notes dropped due to budget
          </p>
          <ul className="space-y-1">
            {entry.dropped_note_ids.map((noteId) => (
              <li key={noteId} className="text-neutral-500">
                <NoteOpenLink noteId={noteId} onOpenNote={onOpenNote} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

function DroppedDetail({ entry }: { entry: ManifestDroppedEntry }) {
  return (
    <div className="rounded-md bg-neutral-50 border border-neutral-200 p-3 text-sm text-neutral-700">
      <p className="font-medium">Not used in the latest reply.</p>
      <p className="text-xs text-neutral-600 mt-1">Reason: {entry.reason}</p>
    </div>
  )
}

function NoteOpenLink({
  noteId,
  onOpenNote,
}: {
  noteId: string
  onOpenNote?: (noteId: string) => void
}) {
  const short = noteId.length > 12 ? `${noteId.slice(0, 12)}…` : noteId
  if (onOpenNote) {
    return (
      <button
        type="button"
        onClick={() => onOpenNote(noteId)}
        className="inline-flex items-center gap-1 text-accent-700 hover:text-accent-900 hover:underline"
      >
        {short}
        <ExternalLink className="size-3" />
      </button>
    )
  }
  return (
    <a
      href={`/sessions/${encodeURIComponent(noteId)}`}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1 text-accent-700 hover:text-accent-900 hover:underline"
    >
      {short}
      <ExternalLink className="size-3" />
    </a>
  )
}
