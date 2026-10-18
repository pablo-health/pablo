// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The "Update the chart" step at sign: what this visit would change on the
 * chart, each one accepted, edited or discarded by the clinician. It is shown
 * only when there is something to decide: a proposed update still pending,
 * or a diagnosis stated in the note that the problem list does not have. It
 * is also shown when the check itself failed, so an empty step is never read
 * as "nothing changed".
 *
 * Signing never waits on it. Whatever is left pending stays on the signed
 * note, where it can still be accepted. Accepting changes the chart, never
 * the note: a signed note keeps the chart it was drafted against.
 */

"use client"

import { AddDiagnosisButton, isListed } from "@/components/sessions/AddToProblemList"
import { useChartProposals } from "@/hooks/useChartProposals"
import { useNoteType } from "@/hooks/useNoteTypes"
import { usePatientProblems } from "@/hooks/useProblems"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import { diagnosisText, statedDiagnoses, type StatedDiagnosis } from "@/lib/statedDiagnoses"
import { isSessionTask, type ChartProposal, type ProposalRun } from "@/types/chartProposals"
import type { Note } from "@/types/notes"
import { NotChecked, isNotChecked } from "./NotChecked"
import { ProposalLedger } from "./ProposalLedger"

function fieldValue(note: Note, section: string, field: string): unknown {
  const read = (content: Record<string, unknown> | null) =>
    (content?.[section] as Record<string, unknown> | undefined)?.[field]
  return read(note.content_edited) ?? read(note.content)
}

/** The diagnoses the note states, from its diagnoses fields, edits included. */
function useStatedDiagnoses(note: Note | undefined): StatedDiagnosis[] {
  const { data: definition } = useNoteType(note?.note_type ?? "", note?.note_type_version)
  if (!note || !definition) return []
  return definition.sections.flatMap((section) =>
    section.fields
      .filter((field) => field.kind === "diagnoses")
      .flatMap((field) => statedDiagnoses(fieldValue(note, section.key, field.key))),
  )
}

export interface ChartUpdates {
  proposals: ChartProposal[]
  /** Stated in the note and not on the problem list. */
  diagnoses: StatedDiagnosis[]
  /** Chart updates not yet decided, and diagnoses not yet added. */
  undecided: number
  /** How the check for chart updates last ended. */
  run: ProposalRun | null
}

export function useChartUpdates(note: Note | undefined): ChartUpdates {
  const { data } = useChartProposals(note?.id)
  const stated = useStatedDiagnoses(note)
  const { data: problems } = usePatientProblems(stated.length ? note?.patient_id : undefined)
  const proposals = data?.data ?? []
  const diagnoses = stated.filter((dx) => !isListed(dx, problems?.data ?? []))
  return {
    proposals,
    diagnoses,
    // Chart updates only: a task left undecided waits on the signed note, and
    // signing past it is not signing "without updating" the chart.
    undecided:
      proposals.filter((p) => p.decision === "pending" && !isSessionTask(p)).length +
      diagnoses.length,
    run: data?.run ?? null,
  }
}

export function UpdateChart({ note, updates }: { note: Note; updates: ChartUpdates }) {
  const { readOnly } = useReadOnlyMode()
  const pending = updates.proposals.filter((p) => p.decision === "pending")
  // What the clinician said they would do goes to their tasks, not the chart:
  // its own section, shown even on a note type that updates no chart.
  const chartPending = pending.filter((p) => !isSessionTask(p))
  const tasks = pending.filter(isSessionTask)
  const notChecked = isNotChecked(updates.run)
  const updatesChart = chartPending.length > 0 || updates.diagnoses.length > 0 || notChecked
  if (!updatesChart && tasks.length === 0) return null

  return (
    <div className="space-y-5">
      {tasks.length > 0 && (
        <ProposalLedger
          noteId={note.id}
          patientId={note.patient_id}
          proposals={tasks}
          readOnly={readOnly}
        />
      )}
      {updatesChart && <UpdateChartSection note={note} updates={updates} pending={chartPending} />}
    </div>
  )
}

function UpdateChartSection({
  note,
  updates,
  pending,
}: {
  note: Note
  updates: ChartUpdates
  pending: ChartProposal[]
}) {
  const { readOnly } = useReadOnlyMode()
  const notChecked = isNotChecked(updates.run)
  return (
    <section aria-label="Update the chart" className="space-y-3" data-testid="update-chart">
      <div>
        <h3 className="text-base font-semibold text-neutral-900">Update the chart</h3>
        <p className="text-sm text-neutral-600">From what was said in this visit.</p>
      </div>
      {notChecked && updates.run && (
        <NotChecked noteId={note.id} run={updates.run} readOnly={readOnly} />
      )}
      {pending.length > 0 && (
        <ProposalLedger
          noteId={note.id}
          patientId={note.patient_id}
          proposals={pending}
          readOnly={readOnly}
        />
      )}
      <ul className="space-y-3">
        {updates.diagnoses.map((dx) => (
          <li
            key={diagnosisText(dx)}
            className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-neutral-200 p-3"
          >
            <span className="text-sm text-neutral-900">
              <span className="font-semibold">Diagnosis: </span>
              {diagnosisText(dx)}
            </span>
            {!readOnly && <AddDiagnosisButton patientId={note.patient_id} noteId={note.id} dx={dx} />}
          </li>
        ))}
      </ul>
    </section>
  )
}
