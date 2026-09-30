// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { format } from "date-fns"
import { Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  seenElsewhere,
  WhichClientsList,
  type ClientQuestionRow,
} from "@/components/calendar/connect/WhichClientsList"
import type {
  NotAddedSession,
  OutsideAnswer,
  OutsideAnswerResult,
  OutsideQuestion,
} from "@/lib/api/outsideSessions"

/** Answers the checked and not-a-client questions; resolves with the result. */
export type SubmitOutsideAnswers = (answers: OutsideAnswer[]) => Promise<OutsideAnswerResult>

interface OutsideSessionsReviewProps {
  open: boolean
  onClose: () => void
  questions: OutsideQuestion[]
  onSubmit: SubmitOutsideAnswers
  /** One event's question, asked from its block rather than the banner:
   * checked from the start, and offering to start its note. */
  single?: boolean
  /** Answer, then start the note for this event. Single mode only.
   * Resolves with the answer's result when there was no session to start. */
  onStartNote?: (answers: OutsideAnswer[]) => Promise<OutsideAnswerResult | void>
}

function notAddedLine(session: NotAddedSession): string {
  const day = format(new Date(session.start_at), "EEE MMM d")
  return `${session.client_name}'s session on ${day} overlaps another appointment, so it wasn't added.`
}

function detailFor(question: OutsideQuestion): string {
  const next = format(new Date(question.next_start_at), "EEE MMM d, h:mm a")
  return question.recurring ? `Repeats · next ${next}` : next
}

function toRow(question: OutsideQuestion): ClientQuestionRow {
  return {
    key: question.key,
    title: question.title,
    detail: detailFor(question),
    aside: `${question.sessions} session${question.sessions === 1 ? "" : "s"}`,
    match: question.match,
    clientInactive: question.client_inactive ?? false,
  }
}

/**
 * "Which of these are clients?" for sessions brought in from the clinician's
 * own calendar. The same list the calendar import uses: a certain match
 * starts checked and needs one confirm, a few possible clients are a pick,
 * anyone else is a new client. Anything left unchecked waits for later.
 *
 * Mounted fresh each time it opens, so its choices start from the questions.
 */
export function OutsideSessionsReview({
  open,
  onClose,
  questions,
  onSubmit,
  single = false,
  onStartNote,
}: OutsideSessionsReviewProps) {
  // A certain match, or a name-only suggestion, starts on that chart and
  // checked: one confirm settles it. A name is shown as a preselected choice
  // beside "New client", never as settled.
  const suggested = (q: OutsideQuestion) =>
    q.match.patient?.patient_id ?? q.match.suggested_patient_id ?? null
  // A colleague's client is never answered from here: the row says who to ask.
  const answerable = (q: OutsideQuestion) => !seenElsewhere(q.match)
  const [checked, setChecked] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(
      questions.map((q) => [q.key, answerable(q) && (single || suggested(q) !== null)])
    )
  )
  const [clientFor, setClientFor] = useState<Record<string, string | null>>(() =>
    Object.fromEntries(questions.map((q) => [q.key, suggested(q)]))
  )
  const [notClient, setNotClient] = useState<Record<string, boolean>>({})
  // An inactive client is made active again on confirm unless unticked.
  const [reactivate, setReactivate] = useState<Record<string, boolean>>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notAdded, setNotAdded] = useState<NotAddedSession[]>([])

  const answers: OutsideAnswer[] = questions
    .filter((q) => answerable(q) && (notClient[q.key] || checked[q.key]))
    .map((q) => {
      // A question about one event answers that event alone.
      const event = q.outside_session_id ? { outside_session_id: q.outside_session_id } : {}
      if (notClient[q.key]) {
        return {
          source: q.source,
          source_identifier: q.source_identifier,
          patient_id: null,
          new_client_name: null,
          not_a_client: true,
          ...event,
        }
      }
      const patientId = clientFor[q.key] ?? null
      const reactivating =
        Boolean(q.client_inactive) &&
        patientId !== null &&
        patientId === q.match.suggested_patient_id &&
        (reactivate[q.key] ?? true)
      return {
        source: q.source,
        source_identifier: q.source_identifier,
        patient_id: patientId,
        new_client_name: patientId ? null : q.title,
        not_a_client: false,
        ...event,
        ...(reactivating ? { reactivate: true } : {}),
      }
    })
  const canStartNote = Boolean(onStartNote) && answers.some((a) => !a.not_a_client)

  const run = async (action: () => Promise<OutsideAnswerResult | void>) => {
    setSaving(true)
    setError(null)
    try {
      const result = await action()
      // Something already booked over a session: say so before closing.
      if (result && result.not_added.length > 0) setNotAdded(result.not_added)
      else onClose()
    } catch {
      setError("Could not save that. Try again in a moment.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-display">
            {single ? "Who is this?" : "Which of these are clients?"}
          </DialogTitle>
          <DialogDescription>
            {single
              ? "Pablo will remember the answer."
              : "Check the ones that are clients. Anything unchecked stays here for later."}
          </DialogDescription>
        </DialogHeader>

        {notAdded.length > 0 ? (
          <div data-testid="outside-not-added" className="space-y-1 text-sm text-amber-700">
            {notAdded.map((session) => (
              <p key={session.outside_session_id}>{notAddedLine(session)}</p>
            ))}
          </div>
        ) : (
          <WhichClientsList
          rows={questions.map(toRow)}
          checked={checked}
          onToggle={(key) => setChecked((current) => ({ ...current, [key]: !current[key] }))}
          clientFor={clientFor}
          onChooseClient={(key, patientId) =>
            setClientFor((current) => ({ ...current, [key]: patientId }))
          }
          notClient={notClient}
          onToggleNotClient={(key) => {
            setNotClient((current) => ({ ...current, [key]: !current[key] }))
            setChecked((current) => ({ ...current, [key]: false }))
          }}
          reactivate={reactivate}
          onToggleReactivate={(key) =>
            setReactivate((current) => ({ ...current, [key]: !(current[key] ?? true) }))
          }
        />
        )}

        {error ? <p className="text-sm text-red-600">{error}</p> : null}

        <div className="flex items-center justify-end gap-2 pt-2">
          {notAdded.length > 0 ? (
            <Button onClick={onClose}>Done</Button>
          ) : null}
          {notAdded.length === 0 && single && onStartNote ? (
            <Button
              variant="outline"
              disabled={saving || !canStartNote}
              onClick={() => run(() => onStartNote(answers))}
            >
              Start note
            </Button>
          ) : null}
          {notAdded.length === 0 ? (
            <Button
              disabled={saving || answers.length === 0}
              onClick={() => run(() => onSubmit(answers))}
            >
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Save
            </Button>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  )
}
