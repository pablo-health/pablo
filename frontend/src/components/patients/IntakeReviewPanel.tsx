// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"

import { ApiError } from "@/lib/api/client"
import {
  downloadIntakeExport,
  intakeExportFilename,
  MAX_CORRECTION_NOTE_LENGTH,
} from "@/lib/api/intakeReview"
import type { IntakeAssignmentStatus, IntakeReviewEvent, IntakeReviewItem, IntakeReviewSignature } from "@/lib/api/intakeReview"
import { useAcceptIntakeAssignment, useEnterIntakeAnswer, useIntakeReview, useRequestIntakeCorrection } from "@/hooks/useIntakeReview"

/**
 * Every sentence this panel shows, in one block. The status sentences describe
 * the status the server sent, and the two progress sentences come from
 * `progress.complete` — reaching this screen says nothing about whether a form
 * is finished, so nothing here computes that.
 */
const COPY = {
  heading: "Intake review",
  loading: "Loading this form…",
  loadError: "We couldn't load this form. Try again in a moment.",
  actionError: "That didn't go through. Try again.",
  status: {
    assigned: "Sent to the patient.",
    in_progress: "The patient has started this.",
    submitted: "Handed in.",
    needs_correction: "Sent back for corrections.",
    accepted: "Accepted.",
    withdrawn: "Withdrawn.",
  } satisfies Record<IntakeAssignmentStatus, string>,
  progressComplete: "Every question has an answer.",
  outstanding: (n: number) => (n === 1 ? "1 question has no answer." : `${n} questions have no answer.`),
  provenance: { patient: "Patient", clinician: "Entered by practice" },
  noAnswer: "No answer",
  earlier: (n: number) => (n === 1 ? "1 earlier answer" : `${n} earlier answers`),
  earlierDetail: (n: number) => (n === 1 ? "One earlier answer was replaced." : `${n} earlier answers were replaced.`),
  enter: "Enter for patient",
  entryLabel: "Answer",
  entrySave: "Save",
  entryCancel: "Cancel",
  correctionsHeading: "Request corrections",
  correctionsSelect: "Choose the questions to send back.",
  noteLabel: "What should the patient redo?",
  send: "Send back",
  accept: "Accept",
  exportLabel: "Export",
  exporting: "Preparing…",
  eventsHeading: "History",
  eventKind: {
    correction_requested: "Corrections requested",
    corrected: "Patient sent corrections",
    accepted: "Accepted",
    clinician_entered: "Answer entered by practice",
  },
  signaturesHeading: "Signatures",
  signedAs: (role: string) => `signed as ${role}`,
}

/** Statuses where a value may still be written down for somebody in the room. */
const ENTRY_STATUSES: IntakeAssignmentStatus[] = ["assigned", "in_progress", "submitted", "needs_correction"]

const LINK = "text-xs font-medium text-primary-600 hover:text-primary-700"
const HEADING = "text-sm font-semibold text-neutral-900"
const CHIP = "mt-1 inline-block rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-600"

function formatMoment(iso: string): string {
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString()
}

/**
 * The answer as a line of text. Answers arrive as an open mapping, so a `text`
 * field is used when there is one and the mapping is spelled out otherwise.
 * Null means the caller renders "no answer" instead.
 */
function formatValue(value: Record<string, unknown> | null): string | null {
  if (!value) return null
  if (typeof value.text === "string" && value.text !== "") return value.text
  const parts = Object.entries(value)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
  return parts.length > 0 ? parts.join(" · ") : null
}

/** The server's own sentence when it wrote one, else a short one of ours. */
function errorMessage(error: Error | null): string | null {
  if (!error) return null
  return error instanceof ApiError && error.message ? error.message : COPY.actionError
}

/**
 * Hand a downloaded file to the browser to save.
 *
 * The route answers with the document itself rather than a link to one, so
 * there is nothing to open in a tab — the blob is turned into a URL that
 * lives exactly as long as the click.
 */
function saveFile(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

interface ReviewItemRowProps {
  item: IntakeReviewItem
  selectable: boolean
  selected: boolean
  onSelect: (itemId: string, checked: boolean) => void
  canEnter: boolean
  saving: boolean
  onSaveEntry: (itemId: string, text: string) => void
}

function ReviewItemRow(props: ReviewItemRowProps) {
  const { item, selectable, selected, onSelect, canEnter, saving, onSaveEntry } = props
  const [showEarlier, setShowEarlier] = useState(false)
  const [entryOpen, setEntryOpen] = useState(false)
  const [entryText, setEntryText] = useState("")
  const id = item.id
  const answer = formatValue(item.value)
  const question = item.label ?? item.key

  return (
    <li className="border-t border-border py-3 first:border-t-0" data-testid={`intake-review-item-${id}`}>
      <div className="flex items-start gap-3">
        {selectable && (
          <input type="checkbox" className="mt-1" checked={selected} aria-label={question}
            onChange={(e) => onSelect(id, e.target.checked)} data-testid={`intake-review-select-${id}`} />
        )}
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-neutral-700">{question}</p>
          <p className="mt-1 whitespace-pre-wrap text-sm text-neutral-900" data-testid={`intake-review-value-${id}`}>
            {answer ?? COPY.noAnswer}
          </p>
          {item.provenance && (
            <span className={CHIP} data-testid={`intake-review-provenance-${id}`}>
              {COPY.provenance[item.provenance]}
            </span>
          )}
          {item.superseded_count > 0 && (
            <div className="mt-2">
              <button type="button" className={LINK} aria-expanded={showEarlier}
                onClick={() => setShowEarlier((open) => !open)}
                data-testid={`intake-review-earlier-toggle-${id}`}>
                {COPY.earlier(item.superseded_count)}
              </button>
              {showEarlier && (
                <p className="mt-1 text-xs text-neutral-500" data-testid={`intake-review-earlier-detail-${id}`}>
                  {COPY.earlierDetail(item.superseded_count)}
                </p>
              )}
            </div>
          )}
          {canEnter && !entryOpen && (
            <button type="button" className={`mt-2 ${LINK}`} onClick={() => setEntryOpen(true)}
              data-testid={`intake-review-enter-${id}`}>
              {COPY.enter}
            </button>
          )}
          {canEnter && entryOpen && (
            <div className="mt-2 flex items-center gap-2">
              <input type="text" className="input flex-1" value={entryText} aria-label={COPY.entryLabel}
                onChange={(e) => setEntryText(e.target.value)}
                data-testid={`intake-review-entry-input-${id}`} />
              <button type="button" className="btn-primary text-xs" disabled={saving || entryText.trim() === ""}
                onClick={() => onSaveEntry(id, entryText.trim())}
                data-testid={`intake-review-entry-save-${id}`}>
                {COPY.entrySave}
              </button>
              <button type="button" className="text-xs text-neutral-500" onClick={() => setEntryOpen(false)}
                data-testid={`intake-review-entry-cancel-${id}`}>
                {COPY.entryCancel}
              </button>
            </div>
          )}
        </div>
      </div>
    </li>
  )
}

/** Absent when nothing has been signed — there is no section for an empty list. */
function SignatureSection({ signatures }: { signatures: IntakeReviewSignature[] }) {
  if (signatures.length === 0) return null
  return (
    <section className="mt-6" data-testid="intake-review-signatures">
      <h3 className={HEADING}>{COPY.signaturesHeading}</h3>
      <ul className="mt-2 space-y-1">
        {signatures.map((s) => (
          <li key={s.id} className="text-sm text-neutral-700" data-testid={`intake-review-signature-${s.id}`}>
            {s.signer_typed_name} {COPY.signedAs(s.signer_role)} · {formatMoment(s.signed_at)}
          </li>
        ))}
      </ul>
    </section>
  )
}

/** What has been asked for and done, newest first. */
function EventSection({ events }: { events: IntakeReviewEvent[] }) {
  if (events.length === 0) return null
  const newestFirst = [...events].sort((a, b) => b.created_at.localeCompare(a.created_at))
  return (
    <section className="mt-6" data-testid="intake-review-events">
      <h3 className={HEADING}>{COPY.eventsHeading}</h3>
      <ul className="mt-2 space-y-1">
        {newestFirst.map((e) => (
          <li key={e.id} className="text-sm text-neutral-700" data-testid={`intake-review-event-${e.id}`}>
            {COPY.eventKind[e.kind] ?? e.kind} · {formatMoment(e.created_at)}
            {e.note_to_patient && (
              <span className="block whitespace-pre-wrap text-neutral-600">{e.note_to_patient}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}

/**
 * The clinician reading a handed-in form back. Everything on screen comes from
 * the server's view of the form: the status sentence, whether every question
 * has an answer, where each answer came from, and which actions are offered. A
 * 409 from any of the three writes means the form has already moved on, and the
 * hook re-reads it rather than leaving a stale screen.
 */
export function IntakeReviewPanel(props: { patientId: string; assignmentId: string }) {
  const { patientId, assignmentId } = props
  const { data, error, isLoading } = useIntakeReview(patientId, assignmentId)
  const correction = useRequestIntakeCorrection(patientId, assignmentId)
  const accept = useAcceptIntakeAssignment(patientId, assignmentId)
  const entry = useEnterIntakeAnswer(patientId, assignmentId)
  const [selected, setSelected] = useState<string[]>([])
  const [note, setNote] = useState("")
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)

  if (isLoading) return <p data-testid="intake-review-loading">{COPY.loading}</p>
  if (error || !data) return <p data-testid="intake-review-load-error">{COPY.loadError}</p>

  const isSubmitted = data.status === "submitted"
  const canEnter = ENTRY_STATUSES.includes(data.status)
  const items = [...data.items].sort((a, b) => a.position - b.position)
  const actionError =
    errorMessage(correction.error ?? accept.error ?? entry.error) ?? exportError
  const pending = correction.isPending || accept.isPending || entry.isPending

  const exportForm = async () => {
    setExporting(true)
    setExportError(null)
    try {
      const file = await downloadIntakeExport(patientId, assignmentId)
      saveFile(file, intakeExportFilename(assignmentId, data.receipt_code))
    } catch {
      setExportError(COPY.actionError)
    } finally {
      setExporting(false)
    }
  }

  const onSelect = (itemId: string, checked: boolean) =>
    setSelected((ids) => (checked ? [...ids, itemId] : ids.filter((i) => i !== itemId)))

  const submitCorrection = () =>
    correction.mutate({ item_ids: selected, note: note.trim() }, {
      onSuccess: () => {
        setSelected([])
        setNote("")
      },
    })

  return (
    <div className="card" data-testid="intake-review-panel">
      <div className="mb-3 flex items-baseline justify-between gap-4">
        <h2 className="text-lg font-semibold text-neutral-900">{COPY.heading}</h2>
        <div className="flex items-baseline gap-3">
          <p className="text-sm text-neutral-500">
            {data.packet_name} v{data.version}
          </p>
          <button type="button" className={LINK} disabled={exporting}
            onClick={() => void exportForm()} data-testid="intake-review-export">
            {exporting ? COPY.exporting : COPY.exportLabel}
          </button>
        </div>
      </div>

      <p className="text-sm text-neutral-700" data-testid="intake-review-status">
        {COPY.status[data.status] ?? data.status}
      </p>
      <p className="text-sm text-neutral-500" data-testid="intake-review-progress">
        {data.progress.complete ? COPY.progressComplete : COPY.outstanding(data.progress.missing.length)}
      </p>
      {actionError && (
        <p role="alert" className="mt-3 text-sm text-red-700" data-testid="intake-review-error">
          {actionError}
        </p>
      )}

      <ul className="mt-4" data-testid="intake-review-items">
        {items.map((item) => (
          <ReviewItemRow key={item.id} item={item} selectable={isSubmitted}
            selected={selected.includes(item.id)} onSelect={onSelect} saving={entry.isPending}
            canEnter={canEnter && item.item_type !== "consent_document"}
            onSaveEntry={(itemId, text) => entry.mutate({ itemId, value: { text } })} />
        ))}
      </ul>

      {isSubmitted && (
        <section className="mt-4 border-t border-border pt-4" data-testid="intake-review-corrections">
          <h3 className={HEADING}>{COPY.correctionsHeading}</h3>
          <p className="mt-1 text-sm text-neutral-500">{COPY.correctionsSelect}</p>
          <label className="mt-2 block text-sm font-medium text-neutral-700" htmlFor="intake-review-note">
            {COPY.noteLabel}
          </label>
          <textarea id="intake-review-note" className="input mt-1 w-full" rows={3} value={note}
            maxLength={MAX_CORRECTION_NOTE_LENGTH} onChange={(e) => setNote(e.target.value)}
            data-testid="intake-review-note" />
          <div className="mt-3 flex gap-2">
            <button type="button" className="btn-primary" onClick={submitCorrection}
              disabled={pending || selected.length === 0 || note.trim() === ""}
              data-testid="intake-review-request">
              {COPY.send}
            </button>
            <button type="button" className="btn-secondary" disabled={pending}
              onClick={() => accept.mutate()} data-testid="intake-review-accept">
              {COPY.accept}
            </button>
          </div>
        </section>
      )}

      <SignatureSection signatures={data.signatures} />
      <EventSection events={data.events} />
    </div>
  )
}
