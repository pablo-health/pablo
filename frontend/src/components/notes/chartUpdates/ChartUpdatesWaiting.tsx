// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The chart's own door to its pending updates: everything the client's notes
 * and accepted intake forms proposed that nobody has decided yet, grouped by
 * note and by form, newest first, at the top of the Chart card. A clinician
 * opening the client before a visit sees what the last notes and the intake
 * would change and decides it here, so the draft that follows is written
 * against a current chart. Shown only when something is waiting; hidden to
 * one line with a click.
 *
 * An intake's update cites the client's answers; each answer's question opens
 * the form at that answer.
 */

"use client"

import { useState } from "react"
import Link from "next/link"
import { ChevronDown, ChevronUp } from "lucide-react"
import { AddDiagnosisButton, isListed } from "@/components/sessions/AddToProblemList"
import { IntakeReviewPanel } from "@/components/patients/IntakeReviewPanel"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog"
import { usePatientChartProposals } from "@/hooks/useChartProposals"
import { useNoteTypeLabel } from "@/hooks/useNoteTypes"
import { usePatientProblems } from "@/hooks/useProblems"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import { sourceSystemName } from "@/lib/noteDisplay"
import { diagnosisText, type StatedDiagnosis } from "@/lib/statedDiagnoses"
import type {
  IntakeChartProposalGroup,
  NoteChartProposalGroup,
} from "@/types/chartProposals"
import { ProposalLedger } from "./ProposalLedger"

function day(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
  })
}

function count(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`
}

/** Where the waiting updates came from: "2 notes", "1 intake", "1 note and 1 intake". */
export function sourcesText(notes: number, intakes: number): string {
  return [
    notes > 0 ? count(notes, "note", "notes") : null,
    intakes > 0 ? count(intakes, "intake", "intakes") : null,
  ]
    .filter(Boolean)
    .join(" and ")
}

export function ChartUpdatesWaiting({ patientId }: { patientId: string }) {
  const { data } = usePatientChartProposals(patientId)
  const [open, setOpen] = useState(true)
  if (!data || data.pending === 0) return null
  const intakes = data.intakes ?? []

  return (
    <section
      aria-label="Chart updates waiting"
      data-testid="chart-updates-waiting"
      className="mb-4 rounded-lg border border-secondary-200 bg-secondary-50/40"
    >
      <div className="flex items-center justify-between gap-3 px-4 py-2.5">
        <p className="text-sm text-neutral-900">
          <span className="font-semibold">
            {data.pending} chart {data.pending === 1 ? "update" : "updates"} waiting
          </span>
          <span className="text-neutral-600">
            {" "}
            from {sourcesText(data.groups.length, intakes.length)}
          </span>
        </p>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? (
            <>
              Hide <ChevronUp className="ml-1 h-4 w-4" aria-hidden />
            </>
          ) : (
            <>
              Review <ChevronDown className="ml-1 h-4 w-4" aria-hidden />
            </>
          )}
        </Button>
      </div>
      {open && (
        <div className="space-y-5 border-t border-secondary-200 px-4 py-4">
          {intakes.map((group) => (
            <IntakeGroup key={group.assignment_id} patientId={patientId} group={group} />
          ))}
          {data.groups.map((group) => (
            <NoteGroup key={group.note_id} patientId={patientId} group={group} />
          ))}
        </div>
      )}
    </section>
  )
}

/**
 * One accepted intake form's waiting updates. The form opens over the chart,
 * at the cited answer when one is clicked, so the clinician can read the
 * answer in place before deciding.
 */
function IntakeGroup({
  patientId,
  group,
}: {
  patientId: string
  group: IntakeChartProposalGroup
}) {
  const { readOnly } = useReadOnlyMode()
  // undefined: closed; "": the whole form; an id: the form at that answer.
  const [openAt, setOpenAt] = useState<string | undefined>(undefined)

  return (
    <div className="space-y-3" data-testid="chart-updates-intake-group">
      <p className="text-sm text-neutral-700">
        From the intake on {day(group.date)}
        <span className="text-neutral-500">, {group.packet_name}</span>
        {" · "}
        <button
          type="button"
          className="text-primary-700 hover:underline"
          onClick={() => setOpenAt("")}
        >
          Open intake
        </button>
      </p>
      <ProposalLedger
        intakeAssignmentId={group.assignment_id}
        patientId={patientId}
        proposals={group.proposals}
        readOnly={readOnly}
        onAnswerClick={setOpenAt}
      />
      <Dialog
        open={openAt !== undefined}
        onOpenChange={(next) => {
          if (!next) setOpenAt(undefined)
        }}
      >
        <DialogContent className="sm:max-w-3xl" data-testid="intake-review-dialog">
          <DialogTitle className="sr-only">{group.packet_name}</DialogTitle>
          {openAt !== undefined && (
            <IntakeReviewPanel
              patientId={patientId}
              assignmentId={group.assignment_id}
              focusItemId={openAt || undefined}
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}

function NoteGroup({ patientId, group }: { patientId: string; group: NoteChartProposalGroup }) {
  const noteTypeLabel = useNoteTypeLabel()
  const { readOnly } = useReadOnlyMode()
  const { data: problems } = usePatientProblems(
    group.source_diagnoses.length ? patientId : undefined,
  )
  // The diagnosis codes the note carried from its system, less those the
  // problem list already has.
  const diagnoses: StatedDiagnosis[] = group.source_diagnoses
    .map((d) => ({ label: d.description || d.code, code: d.code, status: null }))
    .filter((dx) => !isListed(dx, problems?.data ?? []))
  const system = sourceSystemName(group.source_system)
  const href = group.session_id
    ? `/dashboard/sessions/${group.session_id}`
    : `/dashboard/patients/${patientId}/notes/${group.note_id}`
  const origin = system
    ? `imported from ${system}`
    : group.imported
      ? "imported"
      : noteTypeLabel(group.note_type).toLowerCase()

  return (
    <div className="space-y-3" data-testid="chart-updates-group">
      <p className="text-sm text-neutral-700">
        From the note of {day(group.date)}
        <span className="text-neutral-500">, {origin}</span>
        {" · "}
        <Link href={href} className="text-primary-700 hover:underline">
          Open note
        </Link>
      </p>
      <ProposalLedger
        noteId={group.note_id}
        patientId={patientId}
        proposals={group.proposals}
        readOnly={readOnly}
      />
      {diagnoses.length > 0 && (
        <section aria-label="Diagnoses" className="space-y-2">
          <h4 className="border-b border-neutral-200 pb-1 text-sm font-semibold text-neutral-800">
            Diagnoses
          </h4>
          <ul className="divide-y divide-neutral-100">
            {diagnoses.map((dx) => (
              <li
                key={diagnosisText(dx)}
                className="flex flex-wrap items-center justify-between gap-2 py-2"
                data-testid="source-diagnosis"
              >
                <span className="text-sm text-neutral-900">{diagnosisText(dx)}</span>
                {!readOnly && (
                  <AddDiagnosisButton patientId={patientId} noteId={group.note_id} dx={dx} />
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
