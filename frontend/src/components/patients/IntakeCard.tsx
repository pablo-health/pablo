// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { AlertTriangle } from "lucide-react"

import { IntakeArtifacts } from "@/components/patients/IntakeArtifacts"
import { useIntakeArtifacts } from "@/hooks/useIntakeArtifacts"
import { usePatientIntakeSubmissions } from "@/hooks/usePatientIntakeSubmissions"
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
 * The intake form on the chart: what the patient wrote, and nothing they scored.
 *
 * PHQ-9 and GAD-7 answers arrive as outcome measures and the chart already
 * trends and bands them, so this card carries no number and no severity
 * word. What it adds is the part of the form that has nowhere else to go —
 * the reason for the visit, anything the patient said is wrong about their
 * own record, and the files they sent in.
 *
 * The card removes itself when there is nothing to show, including on an
 * error: a chart with no intake form is the ordinary case for a patient who
 * came in before there was one, and an empty box explaining its own absence
 * would be on most charts in the practice. Files count as something to
 * show — a form that asked for a photograph of an insurance card and
 * nothing else still put a file on the chart.
 */
export function IntakeCard({ patientId }: IntakeCardProps) {
  const { data, error } = usePatientIntakeSubmissions(patientId)
  const { groups } = useIntakeArtifacts(patientId)
  const [showEarlier, setShowEarlier] = useState(false)

  const submissions = error ? [] : (data ?? [])
  if (submissions.length === 0 && groups.length === 0) return null

  const [latest, ...earlier] = submissions

  return (
    <div className="card" data-testid="intake-card">
      <div className="mb-4 flex items-baseline justify-between gap-4">
        <h2 className="text-lg font-semibold text-neutral-900">Intake</h2>
        {latest && (
          <p className="text-sm text-neutral-500">
            Submitted {formatDate(latest.submitted_at)}
          </p>
        )}
      </div>

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

      <IntakeArtifacts patientId={patientId} groups={groups} />
    </div>
  )
}
