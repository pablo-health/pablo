// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A signed or imported note's chart updates: what was decided, and whatever
 * was left, which can still be accepted, edited or discarded here. Shown when
 * the note proposed something, or when checking it for updates failed.
 */

"use client"

import { useChartProposals } from "@/hooks/useChartProposals"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
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
  const proposals = data?.data ?? []
  const run = data?.run
  const notChecked = isNotChecked(run)
  if (proposals.length === 0 && !notChecked) return null

  return (
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
  )
}
