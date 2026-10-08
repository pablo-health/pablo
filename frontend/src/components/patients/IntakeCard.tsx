// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { AlertTriangle } from "lucide-react"

import { IntakeArtifacts } from "@/components/patients/IntakeArtifacts"
import { IntakeReviewPanel, intakeStatusText } from "@/components/patients/IntakeReviewPanel"
import { SendIntakeForm } from "@/components/patients/SendIntakeForm"
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog"
import { useIntakeArtifacts, useIntakeAssignments } from "@/hooks/useIntakeArtifacts"
import { usePatientIntakeSubmissions } from "@/hooks/usePatientIntakeSubmissions"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import type { IntakeAssignment } from "@/lib/api/intakeReview"
import type { PatientIntakeSubmission } from "@/types/patientIntakeSubmissions"

interface IntakeCardProps {
  patientId: string
}

function formatDate(iso: string): string {
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleDateString()
}

function needsAttention(submission: PatientIntakeSubmission): boolean {
  return (
    !submission.name_confirmed ||
    !submission.dob_confirmed ||
    !!submission.corrections
  )
}

/**
 * What the patient said about the name and date of birth on file.
 *
 * Only reached when something needs attention, so the sentence names the
 * specific field rather than making the clinician work out which one. The
 * confirm flags are an attestation: a "no" means the patient said the chart
 * is wrong, and nothing edited the chart on the strength of it.
 */
function attestationSummary(submission: PatientIntakeSubmission): string {
  if (!submission.name_confirmed && !submission.dob_confirmed) {
    return "Did not confirm the name or date of birth on file."
  }
  if (!submission.name_confirmed) return "Did not confirm the name on file."
  if (!submission.dob_confirmed) {
    return "Did not confirm the date of birth on file."
  }
  return "Sent a correction."
}

function SubmissionBody({
  submission,
}: {
  submission: PatientIntakeSubmission
}) {
  return (
    <div className="space-y-3">
      <div>
        <p className="text-sm font-medium text-neutral-700">
          What brings you in?
        </p>
        <p className="mt-1 whitespace-pre-wrap text-sm text-neutral-900">
          {submission.reason_text}
        </p>
      </div>

      {needsAttention(submission) && (
        <div
          role="note"
          className="rounded-lg border border-amber-200 bg-amber-50 p-3"
        >
          <div className="flex items-start gap-2">
            <AlertTriangle
              aria-hidden="true"
              className="mt-0.5 h-4 w-4 shrink-0 text-amber-700"
            />
            <div className="space-y-1">
              <p className="text-sm font-medium text-amber-900">
                {attestationSummary(submission)}
              </p>
              {submission.corrections && (
                <p className="whitespace-pre-wrap text-sm text-amber-800">
                  {submission.corrections}
                </p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/**
 * One form on the list, and the review it opens.
 *
 * The row carries the form's name and the server's own sentence about where
 * it has got to. Clicking it opens the whole form over the chart, drawn the
 * way the patient filled it in, with the review's actions beside it and a
 * print button that prints the form alone. Reading it is a second audited
 * read, so it waits for somebody to open it.
 *
 * Every status opens, not only a handed-in one. The panel is what decides
 * which actions a status offers — corrections and acceptance appear on a
 * form that has been handed in and on no other — and a row that refused to
 * open would be a second place deciding the same thing, in a screen that
 * could only ever disagree with the server.
 */
function AssignmentRow({
  patientId,
  assignment,
}: {
  patientId: string
  assignment: IntakeAssignment
}) {
  const [open, setOpen] = useState(false)
  const people = usePeopleTerm()

  return (
    <div data-testid={`intake-assignment-${assignment.id}`}>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        data-testid={`intake-assignment-open-${assignment.id}`}
        className="flex w-full items-baseline justify-between gap-4 rounded-lg border border-border px-3 py-2 text-left hover:border-neutral-300"
      >
        <span className="text-sm font-medium text-neutral-900">
          {assignment.packet_name} v{assignment.version}
        </span>
        <span className="text-sm text-neutral-500">
          {intakeStatusText(people)[assignment.status] ?? assignment.status}
        </span>
      </button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-3xl" data-testid="intake-review-dialog">
          <DialogTitle className="sr-only">
            {assignment.packet_name} v{assignment.version}
          </DialogTitle>
          {open && <IntakeReviewPanel patientId={patientId} assignmentId={assignment.id} />}
        </DialogContent>
      </Dialog>
    </div>
  )
}

/**
 * The intake form on the chart: what the patient wrote and chose.
 *
 * PHQ-9 and GAD-7 totals arrive as outcome measures and the chart already
 * trends and bands them, so this card carries no total and no severity word.
 * Opening a form shows it the way the patient filled it in, each measure
 * item with the answer that was chosen — the one thing the trend does not
 * show. The card itself adds what has nowhere else to go: the reason for the
 * visit, anything the patient said is wrong about their own record, and the
 * files they sent in.
 *
 * It is the chart's Intake tab, so it always renders: the tab is where an
 * intake is started, and a practice that has not set up forms finds that out
 * here, from the send flow, rather than from a card that is not there.
 *
 * A form counts as something to show whether or not it has been handed in or
 * collected a file — one that asked for a photograph of an insurance card and
 * nothing else still put a file on the chart, and one still out with the
 * patient is the thing somebody opening this chart before a first session is
 * looking for.
 */
export function IntakeCard({ patientId }: IntakeCardProps) {
  const { data, error } = usePatientIntakeSubmissions(patientId)
  const { groups } = useIntakeArtifacts(patientId)
  const { data: assignmentRows } = useIntakeAssignments(patientId)
  const [showEarlier, setShowEarlier] = useState(false)
  const people = usePeopleTerm()

  const submissions = error ? [] : (data ?? [])
  const assignments = assignmentRows ?? []
  // Said only once both reads have answered: "nothing has been sent" while
  // they are in flight would be a claim nobody checked. Files hang off
  // assignments, so no assignments means no files to wait for.
  const settled = (data !== undefined || !!error) && assignmentRows !== undefined
  const empty =
    settled && submissions.length === 0 && groups.length === 0 && assignments.length === 0

  const [latest, ...earlier] = submissions

  return (
    <div data-testid="intake-card">
      <div className="mb-4 flex items-center justify-between gap-4">
        {latest ? (
          <p className="text-sm text-neutral-500">
            Submitted {formatDate(latest.submitted_at)}
          </p>
        ) : (
          <span />
        )}
        <SendIntakeForm patientId={patientId} />
      </div>

      {empty && (
        <p className="text-sm text-neutral-600" data-testid="intake-empty">
          No packets have been sent to this {people.one} yet.
        </p>
      )}

      {latest && <SubmissionBody submission={latest} />}

      {earlier.length > 0 && (
        <div className="mt-4 border-t border-border pt-4">
          <button
            type="button"
            onClick={() => setShowEarlier((open) => !open)}
            aria-expanded={showEarlier}
            className="text-sm font-medium text-primary-600 hover:text-primary-700"
          >
            {showEarlier ? "Hide" : "Show"} earlier submissions (
            {earlier.length})
          </button>

          {showEarlier && (
            <div className="mt-4 space-y-4">
              {earlier.map((submission) => (
                <div
                  key={submission.id}
                  className="rounded-lg border border-border p-4"
                >
                  <p className="mb-2 text-sm text-neutral-500">
                    Submitted {formatDate(submission.submitted_at)}
                  </p>
                  <SubmissionBody submission={submission} />
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {assignments.length > 0 && (
        <div
          className="mt-4 space-y-3 border-t border-border pt-4"
          data-testid="intake-assignments"
        >
          <h3 className="text-sm font-semibold text-neutral-900">Packets</h3>
          {assignments.map((assignment) => (
            <AssignmentRow
              key={assignment.id}
              patientId={patientId}
              assignment={assignment}
            />
          ))}
        </div>
      )}

      <IntakeArtifacts patientId={patientId} groups={groups} />
    </div>
  )
}
