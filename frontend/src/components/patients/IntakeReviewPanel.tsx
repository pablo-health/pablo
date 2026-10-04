// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"

import { ApiError } from "@/lib/api/client"
import type { PeopleWords } from "@/lib/peopleTerm"
import { saveFile } from "@/lib/saveFile"
import {
  downloadIntakeExport,
  intakeExportFilename,
  MAX_CORRECTION_NOTE_LENGTH,
} from "@/lib/api/intakeReview"
import type {
  IntakeAssignmentStatus,
  IntakeReview,
  IntakeReviewEvent,
  IntakeReviewSignature,
} from "@/lib/api/intakeReview"
import { useIntakeAssignmentArtifacts } from "@/hooks/useIntakeArtifacts"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { useAcceptIntakeAssignment, useEnterIntakeAnswer, useIntakeReview, useRequestIntakeCorrection } from "@/hooks/useIntakeReview"
import { IntakeReviewItemRow } from "./IntakeReviewItemRow"
import { artifactsFor, readOnlySource, shownKeys } from "./intakeReadOnly"

/**
 * What each status the server sends means, in a sentence.
 *
 * Exported because the chart lists forms beside the one it has open, and a
 * row and the panel it opens must not describe the same status in two
 * different ways. Every sentence is about the status the server sent; none
 * of them is computed here.
 */
export function intakeStatusText(people: PeopleWords) {
  return {
    assigned: `Sent to the ${people.one}.`,
    in_progress: `The ${people.one} has started this.`,
    submitted: "Handed in.",
    needs_correction: "Sent back for corrections.",
    accepted: "Accepted.",
    withdrawn: "Withdrawn.",
  } satisfies Record<IntakeAssignmentStatus, string>
}

/**
 * Every sentence this panel shows, in one block. The status sentences describe
 * the status the server sent, and the two progress sentences come from
 * `progress.complete` — reaching this screen says nothing about whether a form
 * is finished, so nothing here computes that.
 */
const COPY = {
  loading: "Loading this form…",
  loadError: "We couldn't load this form. Try again in a moment.",
  actionError: "That didn't go through. Try again.",
  progressComplete: "Every question has an answer.",
  outstanding: (n: number) => (n === 1 ? "1 question has no answer." : `${n} questions have no answer.`),
  correctionsHeading: "Request corrections",
  correctionsSelect: "Choose the questions to send back.",
  noteLabel: (people: PeopleWords) => `What should the ${people.one} redo?`,
  send: "Send back",
  accept: "Accept",
  exportLabel: "Export",
  print: "Print / Save as PDF",
  submitted: (day: string) => `Submitted ${day}`,
  exporting: "Preparing…",
  eventsHeading: "History",
  eventKind: (people: PeopleWords): Record<string, string> => ({
    correction_requested: "Corrections requested",
    corrected: `${people.One} sent corrections`,
    accepted: "Accepted",
    clinician_entered: "Answer entered by practice",
  }),
  signaturesHeading: "Signatures",
  signedAs: (role: string) => `signed as ${role}`,
}

/** Statuses where a value may still be written down for somebody in the room. */
const ENTRY_STATUSES: IntakeAssignmentStatus[] = ["assigned", "in_progress", "submitted", "needs_correction"]

const LINK = "text-xs font-medium text-primary-600 hover:text-primary-700"
const HEADING = "text-sm font-semibold text-neutral-900"

function formatMoment(iso: string): string {
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString()
}

function formatDay(iso: string): string {
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleDateString()
}

/** The server's own sentence when it wrote one, else a short one of ours. */
function errorMessage(error: Error | null): string | null {
  if (!error) return null
  return error instanceof ApiError && error.message ? error.message : COPY.actionError
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
  const people = usePeopleTerm()
  if (events.length === 0) return null
  const newestFirst = [...events].sort((a, b) => b.created_at.localeCompare(a.created_at))
  return (
    <section className="mt-6" data-testid="intake-review-events">
      <h3 className={HEADING}>{COPY.eventsHeading}</h3>
      <ul className="mt-2 space-y-1">
        {newestFirst.map((e) => (
          <li key={e.id} className="text-sm text-neutral-700" data-testid={`intake-review-event-${e.id}`}>
            {COPY.eventKind(people)[e.kind] ?? e.kind} · {formatMoment(e.created_at)}
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
 * Who the printed copy is about, above the form. Printed, unlike the review
 * chrome around it, because a page that leaves the chart has to say whose it
 * is, which practice asked, and when it was handed in.
 */
function FormHeading({ review }: { review: IntakeReview }) {
  const { identity } = review.form
  return (
    <header className="mb-4 border-b border-border pb-3" data-testid="intake-review-print-heading">
      {review.practice_name && <p className="text-sm text-neutral-500">{review.practice_name}</p>}
      <h2 className="text-lg font-semibold text-neutral-900">
        {identity.first_name} {identity.last_name}
      </h2>
      <p className="text-sm text-neutral-600">
        {review.packet_name} v{review.version}
        {review.submitted_at && ` · ${COPY.submitted(formatDay(review.submitted_at))}`}
      </p>
    </header>
  )
}

/**
 * The clinician reading a handed-in form back. Each question is drawn by the
 * portal renderer the patient answered on, read-only, so the chart shows what
 * was on the patient's screen; around it sit the review's own facts and
 * actions, none of which print.
 *
 * Everything on screen comes from the server's view of the form: the status
 * sentence, whether every question has an answer, where each answer came
 * from, and which actions are offered. A 409 from any of the three writes
 * means the form has already moved on, and the hook re-reads it rather than
 * leaving a stale screen.
 *
 * Two ways out of the chart, for two different jobs. Print draws this page
 * — the form as the patient saw it — through the browser, which is also how
 * it becomes a PDF. Export is the filed record: one self-contained document
 * with measure totals, every replaced answer and each moment in the
 * practice's timezone, which is what a release of information or a referral
 * needs and a copy of a screen is not.
 */
export function IntakeReviewPanel(props: { patientId: string; assignmentId: string }) {
  const { patientId, assignmentId } = props
  const { data, error, isLoading } = useIntakeReview(patientId, assignmentId)
  const correction = useRequestIntakeCorrection(patientId, assignmentId)
  const accept = useAcceptIntakeAssignment(patientId, assignmentId)
  const entry = useEnterIntakeAnswer(patientId, assignmentId)
  const { data: files } = useIntakeAssignmentArtifacts(patientId, assignmentId)
  const [selected, setSelected] = useState<string[]>([])
  const [note, setNote] = useState("")
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)
  const people = usePeopleTerm()

  if (isLoading) return <p data-testid="intake-review-loading">{COPY.loading}</p>
  if (error || !data) return <p data-testid="intake-review-load-error">{COPY.loadError}</p>

  const isSubmitted = data.status === "submitted"
  const canEnter = ENTRY_STATUSES.includes(data.status)
  const items = [...data.items].sort((a, b) => a.position - b.position)
  const shown = shownKeys(data, items)
  const source = readOnlySource(data, files ?? [])
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
    <div data-testid="intake-review-panel" data-intake-print="">
      <FormHeading review={data} />

      <div className="print:hidden">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <div>
            <p className="text-sm text-neutral-700" data-testid="intake-review-status">
              {intakeStatusText(people)[data.status] ?? data.status}
            </p>
            <p className="text-sm text-neutral-500" data-testid="intake-review-progress">
              {data.progress.complete ? COPY.progressComplete : COPY.outstanding(data.progress.missing.length)}
            </p>
          </div>
          <div className="flex items-baseline gap-3">
            <button type="button" className={LINK} onClick={() => window.print()}
              data-testid="intake-review-print">
              {COPY.print}
            </button>
            <button type="button" className={LINK} disabled={exporting}
              onClick={() => void exportForm()} data-testid="intake-review-export">
              {exporting ? COPY.exporting : COPY.exportLabel}
            </button>
          </div>
        </div>
        {actionError && (
          <p role="alert" className="mt-3 text-sm text-red-700" data-testid="intake-review-error">
            {actionError}
          </p>
        )}
      </div>

      <ul className="mt-2" data-testid="intake-review-items">
        {items.map((item) => (
          <IntakeReviewItemRow key={item.id} item={item} form={data.form} readOnly={source}
            artifacts={artifactsFor(item.id, assignmentId, files ?? [])} shown={shown[item.key] !== false}
            selectable={isSubmitted} selected={selected.includes(item.id)} onSelect={onSelect}
            saving={entry.isPending} canEnter={canEnter}
            onSaveEntry={(itemId, text) => entry.mutate({ itemId, value: { text } })} />
        ))}
      </ul>

      {isSubmitted && (
        <section className="mt-4 border-t border-border pt-4 print:hidden" data-testid="intake-review-corrections">
          <h3 className={HEADING}>{COPY.correctionsHeading}</h3>
          <p className="mt-1 text-sm text-neutral-500">{COPY.correctionsSelect}</p>
          <label className="mt-2 block text-sm font-medium text-neutral-700" htmlFor="intake-review-note">
            {COPY.noteLabel(people)}
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

      <div className="print:hidden">
        <SignatureSection signatures={data.signatures} />
        <EventSection events={data.events} />
      </div>
    </div>
  )
}
