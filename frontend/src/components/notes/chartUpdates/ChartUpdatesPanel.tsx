// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A signed or imported note's chart updates: what was decided, and whatever
 * was left, which can still be accepted, edited or discarded here. Shown when
 * the note proposed something, or when checking it for updates failed. What
 * the clinician said in the session they would do is its own card, since it
 * goes to their tasks rather than the chart.
 */

"use client"

import { useChartProposals } from "@/hooks/useChartProposals"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import { isSessionTask } from "@/types/chartProposals"
import type { Note } from "@/types/notes"
import { NotChecked, isNotChecked } from "./NotChecked"
import { ProposalLedger } from "./ProposalLedger"
import type { EvidenceHandler } from "./ProposalRow"

export function ChartUpdatesPanel({
  note,
  onEvidenceClick,
}: {
  note: Note
  /** Lights the cited lines in the page's source pane, when it has one. */
  onEvidenceClick?: EvidenceHandler
}) {
  const { data } = useChartProposals(note.id)
  const { readOnly } = useReadOnlyMode()
  const all = data?.data ?? []
  // What the clinician said they would do goes to their tasks, not the chart.
  const proposals = all.filter((p) => !isSessionTask(p))
  const tasks = all.filter(isSessionTask)
  const run = data?.run
  const notChecked = isNotChecked(run)
  const updatesChart = proposals.length > 0 || notChecked
  if (!updatesChart && tasks.length === 0) return null

  return (
    <>
      {tasks.length > 0 && (
        <section aria-label="Tasks" className="card space-y-3" data-testid="session-tasks">
          <ProposalLedger
            noteId={note.id}
            patientId={note.patient_id}
            proposals={tasks}
            readOnly={readOnly}
            onEvidenceClick={onEvidenceClick}
          />
        </section>
      )}
      {updatesChart && (
        <section aria-label="Chart updates" className="card space-y-3" data-testid="chart-updates">
          <h3 className="text-lg font-semibold text-neutral-900">Chart updates from this note</h3>
          {notChecked && run && <NotChecked noteId={note.id} run={run} readOnly={readOnly} />}
          {proposals.length > 0 && (
            <ProposalLedger
              noteId={note.id}
              patientId={note.patient_id}
              proposals={proposals}
              readOnly={readOnly}
              onEvidenceClick={onEvidenceClick}
            />
          )}
        </section>
      )}
    </>
  )
}
