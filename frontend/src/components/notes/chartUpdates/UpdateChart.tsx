// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The "Update the chart" step at sign: what this visit would change on the
 * chart, each one accepted, edited or discarded by the clinician. It is shown
 * only when there is something to decide: a proposed update still pending,
 * or a diagnosis stated in the note that the problem list does not have.
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
import type { ChartProposal } from "@/types/chartProposals"
import type { Note } from "@/types/notes"
import { ProposalRow } from "./ProposalRow"

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
  /** Proposals not yet decided, and diagnoses not yet added. */
  undecided: number
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
    undecided: proposals.filter((p) => p.decision === "pending").length + diagnoses.length,
  }
}

export function UpdateChart({ note, updates }: { note: Note; updates: ChartUpdates }) {
  const { readOnly } = useReadOnlyMode()
  const pending = updates.proposals.filter((p) => p.decision === "pending")
  if (pending.length === 0 && updates.diagnoses.length === 0) return null

  return (
    <section aria-label="Update the chart" className="space-y-3" data-testid="update-chart">
      <div>
        <h3 className="text-base font-semibold text-neutral-900">Update the chart</h3>
        <p className="text-sm text-neutral-600">From what was said in this visit.</p>
      </div>
      <ul className="space-y-3">
        {pending.map((proposal) => (
          <ProposalRow
            key={proposal.id}
            noteId={note.id}
            patientId={note.patient_id}
            proposal={proposal}
            readOnly={readOnly}
          />
        ))}
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
