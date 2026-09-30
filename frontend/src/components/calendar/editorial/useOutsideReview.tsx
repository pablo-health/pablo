// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useState, type ReactNode } from "react"
import { useRouter } from "next/navigation"
import { useAnswerOutsideSessions, useOutsideQuestions } from "@/hooks/useOutsideSessions"
import {
  startSessionFromAppointment,
  type OutsideAnswer,
  type OutsideQuestion,
  type OutsideSession,
} from "@/lib/api/outsideSessions"
import { OutsideSessionsReview, type SubmitOutsideAnswers } from "./OutsideSessionsReview"

type ReviewState = { mode: "all" } | { mode: "single"; session: OutsideSession } | null

/** The question for one event: its own, when its title is asked about per
 * event; otherwise its series' or title's; or a bare one while the question
 * list is still loading. */
function questionFor(session: OutsideSession, questions: OutsideQuestion[]): OutsideQuestion {
  const sameIdentifier = (q: OutsideQuestion) =>
    q.source === session.source && q.source_identifier === session.source_identifier
  return (
    questions.find((q) => q.outside_session_id === session.id) ??
    questions.find((q) => sameIdentifier(q) && !q.outside_session_id) ?? {
      key: `${session.source}|${session.source_identifier}`,
      source: session.source,
      source_identifier: session.source_identifier,
      title: session.title,
      recurring: false,
      sessions: 1,
      next_start_at: session.start_at,
      match: { patient: null, possible: [], suggested_patient_id: null },
    }
  )
}

/** One event's question, with "Start note": answer, then start that event's
 * session and open it. */
function SingleOutsideReview({
  session,
  questions,
  onClose,
  onSubmit,
}: {
  session: OutsideSession
  questions: OutsideQuestion[]
  onClose: () => void
  onSubmit: SubmitOutsideAnswers
}) {
  const router = useRouter()
  const startNote = async (answers: OutsideAnswer[]) => {
    const result = await onSubmit(answers)
    const created = result.appointments.find((a) => a.outside_session_id === session.id)
    // Booked over by something else: the review says so instead.
    if (!created && result.not_added.length > 0) return result
    if (!created) throw new Error("No appointment was made for this session")
    const started = await startSessionFromAppointment(created.appointment_id)
    router.push(`/dashboard/sessions/${started.id}`)
  }
  return (
    <OutsideSessionsReview
      open
      single
      onClose={onClose}
      questions={[questionFor(session, questions)]}
      onSubmit={onSubmit}
      onStartNote={startNote}
    />
  )
}

/**
 * The "who is this?" review for sessions from the clinician's own calendar:
 * the whole list from the banner, or one event from its block.
 */
export function useOutsideReview(): {
  count: number
  fromGoogleOnly: boolean
  openAll: () => void
  openSingle: (session: OutsideSession) => void
  dialog: ReactNode
} {
  const { data } = useOutsideQuestions()
  const answer = useAnswerOutsideSessions()
  const [review, setReview] = useState<ReviewState>(null)
  const questions = data?.questions ?? []

  const close = useCallback(() => setReview(null), [])
  const submit = useCallback<SubmitOutsideAnswers>(
    (answers) => answer.mutateAsync(answers),
    [answer]
  )

  let dialog: ReactNode = null
  if (review?.mode === "all") {
    dialog = (
      <OutsideSessionsReview open onClose={close} questions={questions} onSubmit={submit} />
    )
  } else if (review?.mode === "single") {
    dialog = (
      <SingleOutsideReview
        session={review.session}
        questions={questions}
        onClose={close}
        onSubmit={submit}
      />
    )
  }

  return {
    count: data?.count ?? 0,
    fromGoogleOnly: questions.every((q) => q.source === "google_calendar"),
    openAll: () => setReview({ mode: "all" }),
    openSingle: (session) => setReview({ mode: "single", session }),
    dialog,
  }
}
