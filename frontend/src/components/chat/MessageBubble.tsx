// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Single user or assistant turn (§13.9 of design doc), with the inline
 * per-message manifest disclosure (§13.3) folded into the assistant
 * variant.
 *
 * Visual contract:
 *   - User: right-aligned, honey-tinted, ``rounded-2xl rounded-br-md``.
 *   - Assistant: left-aligned, white card, soft border + shadow,
 *     ``rounded-2xl rounded-bl-md``. While streaming, a TypingDots tail
 *     sits inside the bubble.
 *   - Manifest disclosure: small caret line under the assistant bubble.
 *     Expands inline; the panel never opens a new layer for it.
 */

import { useState } from "react"
import {
  Check,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  FileText,
  Loader2,
} from "lucide-react"

import { cn } from "@/lib/utils"
import { saveMessageAsNote } from "@/lib/chat/api"
import type { ChatMessage, ManifestIncludedEntry, SourceKey } from "@/lib/chat/types"
import { SOURCE_META } from "@/lib/chat/sourceMeta"

import { TypingDots } from "./TypingDots"

interface MessageBubbleProps {
  message: ChatMessage
  /** True while ``delta`` events are still landing for this message. */
  streaming?: boolean
  /**
   * Optional callback for clicking a note-id link in the manifest.
   * Default opens ``/sessions/{noteId}`` in a new tab.
   */
  onOpenNote?: (noteId: string) => void
  /**
   * Notification hook fired after the message is persisted as a
   * standalone note (THERAPY-rg5w). Receives the new note id.
   */
  onSavedAsNote?: (noteId: string, messageId: string) => void
}

export function MessageBubble({
  message,
  streaming = false,
  onOpenNote,
  onSavedAsNote,
}: MessageBubbleProps) {
  if (message.role === "user") {
    return (
      <div
        data-slot="chat-message"
        data-role="user"
        className="flex justify-end"
      >
        <div
          className={cn(
            "max-w-[85%] rounded-2xl rounded-br-md bg-primary-100 px-4 py-2.5",
            "text-sm leading-relaxed text-neutral-900 whitespace-pre-wrap break-words",
          )}
        >
          {message.content}
        </div>
      </div>
    )
  }

  return (
    <div data-slot="chat-message" data-role="assistant" className="flex flex-col items-start gap-1">
      <div
        className={cn(
          "max-w-[85%] rounded-2xl rounded-bl-md bg-card border border-neutral-200 shadow-sm",
          "px-4 py-2.5 text-sm leading-relaxed text-neutral-900 whitespace-pre-wrap break-words",
        )}
      >
        {message.content}
        {streaming ? (
          <span className="ml-2 align-middle inline-flex">
            <TypingDots />
          </span>
        ) : null}
      </div>
      {!streaming ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <ManifestDisclosure message={message} onOpenNote={onOpenNote} />
          {/* Save-as-note is only meaningful for assistant turns that
              actually persisted on the server — local optimistic ids
              (prefix ``local-``) never have a corresponding row to
              promote, so we hide the action until ``meta`` rewrites
              the id. */}
          {!message.id.startsWith("local-") && message.content.trim() ? (
            <SaveAsNoteButton message={message} onSavedAsNote={onSavedAsNote} />
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Save as note (§THERAPY-rg5w)
// ---------------------------------------------------------------------------

interface SaveAsNoteButtonProps {
  message: ChatMessage
  onSavedAsNote?: (noteId: string, messageId: string) => void
}

function SaveAsNoteButton({ message, onSavedAsNote }: SaveAsNoteButtonProps) {
  const [state, setState] = useState<
    | { kind: "idle" }
    | { kind: "saving" }
    | { kind: "saved"; noteId: string }
    | { kind: "error"; message: string }
  >({ kind: "idle" })

  const handleClick = async () => {
    if (state.kind === "saving" || state.kind === "saved") return
    setState({ kind: "saving" })
    try {
      const note = await saveMessageAsNote(message.conversation_id, message.id)
      setState({ kind: "saved", noteId: note.id })
      onSavedAsNote?.(note.id, message.id)
    } catch (exc) {
      setState({
        kind: "error",
        message: exc instanceof Error ? exc.message : "Failed to save",
      })
    }
  }

  if (state.kind === "saved") {
    return (
      <span
        data-slot="chat-save-as-note-saved"
        className="inline-flex items-center gap-1 text-xs text-green-700"
        title={`Saved as note ${state.noteId}`}
      >
        <Check className="size-3" /> Saved as note
      </span>
    )
  }

  return (
    <button
      type="button"
      onClick={() => void handleClick()}
      disabled={state.kind === "saving"}
      data-testid="chat-save-as-note"
      className={cn(
        "inline-flex items-center gap-1 rounded px-1 -mx-1 text-xs text-neutral-600",
        "hover:bg-neutral-100 transition-colors disabled:opacity-50",
        state.kind === "error" && "text-red-700",
      )}
      title={
        state.kind === "error"
          ? `Save failed: ${state.message}`
          : "Save this answer as a chart note"
      }
    >
      {state.kind === "saving" ? (
        <Loader2 className="size-3 animate-spin" />
      ) : (
        <FileText className="size-3" />
      )}
      {state.kind === "error" ? "Retry save" : "Save as note"}
    </button>
  )
}

// ---------------------------------------------------------------------------
// Manifest disclosure (§13.3)
// ---------------------------------------------------------------------------

interface ManifestDisclosureProps {
  message: ChatMessage
  onOpenNote?: (noteId: string) => void
}

function ManifestDisclosure({ message, onOpenNote }: ManifestDisclosureProps) {
  const [open, setOpen] = useState(false)
  const manifest = message.context_manifest
  if (!manifest || manifest.sources_included.length === 0) {
    return null
  }

  const summary = summarizeManifest(manifest.sources_included, manifest.total_tokens_est)

  return (
    <div data-slot="chat-manifest" className="text-xs text-neutral-600">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="inline-flex items-center gap-1 rounded px-1 -mx-1 hover:bg-neutral-100 transition-colors"
        aria-expanded={open}
      >
        {open ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
        <span>Based on {summary}</span>
      </button>
      {open ? (
        <ul className="mt-1 ml-4 space-y-1 border-l border-neutral-200 pl-3 py-1">
          {manifest.sources_included.map((entry) => (
            <ManifestEntryRow
              key={entry.source_key}
              entry={entry}
              onOpenNote={onOpenNote}
            />
          ))}
        </ul>
      ) : null}
    </div>
  )
}

function ManifestEntryRow({
  entry,
  onOpenNote,
}: {
  entry: ManifestIncludedEntry
  onOpenNote?: (noteId: string) => void
}) {
  const meta = SOURCE_META[entry.source_key as SourceKey]
  const Icon = meta?.icon
  const label = meta?.label ?? entry.source_key

  return (
    <li className="flex items-start gap-2">
      {Icon ? <Icon className="size-3 mt-0.5 shrink-0 text-neutral-500" /> : null}
      <div className="flex-1 min-w-0">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="font-medium text-neutral-700">{label}</span>
          {typeof entry.row_count === "number" && entry.row_count > 0 ? (
            <span className="text-neutral-500">
              {entry.row_count} {entry.row_count === 1 ? "item" : "items"}
            </span>
          ) : null}
          {entry.rows_dropped && entry.rows_dropped > 0 ? (
            <span className="text-neutral-500">
              · {entry.rows_dropped} dropped to fit
            </span>
          ) : null}
          <span className="text-neutral-500">· {formatTokens(entry.tokens_est)} tokens</span>
        </div>
        {entry.note_ids && entry.note_ids.length > 0 ? (
          <ul className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5">
            {entry.note_ids.map((noteId) => (
              <li key={noteId}>
                <NoteLink noteId={noteId} onOpenNote={onOpenNote} />
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </li>
  )
}

function NoteLink({
  noteId,
  onOpenNote,
}: {
  noteId: string
  onOpenNote?: (noteId: string) => void
}) {
  const short = noteId.length > 8 ? `${noteId.slice(0, 8)}…` : noteId
  if (onOpenNote) {
    return (
      <button
        type="button"
        onClick={() => onOpenNote(noteId)}
        className="inline-flex items-center gap-0.5 text-accent-700 hover:text-accent-900 hover:underline"
      >
        {short}
        <ExternalLink className="size-2.5" />
      </button>
    )
  }
  return (
    <a
      href={`/sessions/${encodeURIComponent(noteId)}`}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-0.5 text-accent-700 hover:text-accent-900 hover:underline"
    >
      {short}
      <ExternalLink className="size-2.5" />
    </a>
  )
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function summarizeManifest(
  entries: ManifestIncludedEntry[],
  totalTokens: number,
): string {
  const labels = entries
    .map((entry) => {
      const meta = SOURCE_META[entry.source_key as SourceKey]
      const baseLabel = meta?.label.toLowerCase() ?? entry.source_key
      if (typeof entry.row_count === "number" && entry.row_count > 1) {
        return `${entry.row_count} ${baseLabel}`
      }
      return baseLabel
    })
    // Cap visible labels to keep the line readable.
    .slice(0, 4)

  const overflow = entries.length - labels.length
  const labelPart =
    overflow > 0 ? `${labels.join(", ")} +${overflow} more` : labels.join(", ")
  return `${labelPart} · ${formatTokens(totalTokens)} tokens`
}

function formatTokens(n: number): string {
  if (n < 1000) return String(n)
  return `${(n / 1000).toFixed(1)}k`
}
