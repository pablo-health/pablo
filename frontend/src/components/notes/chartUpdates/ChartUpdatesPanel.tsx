// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A signed note's chart updates: what was decided at sign, and whatever was
 * left, which can still be accepted, edited or discarded here. Shown only
 * when the note proposed something.
 */

"use client"

import { useChartProposals } from "@/hooks/useChartProposals"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import type { Note } from "@/types/notes"
import { ProposalRow } from "./ProposalRow"

export function ChartUpdatesPanel({ note }: { note: Note }) {
  const { data } = useChartProposals(note.id)
  const { readOnly } = useReadOnlyMode()
  const proposals = data?.data ?? []
  if (proposals.length === 0) return null

  return (
    <section aria-label="Chart updates" className="card space-y-3" data-testid="chart-updates">
      <h3 className="text-lg font-semibold text-neutral-900">Chart updates from this note</h3>
      <ul className="space-y-3">
        {proposals.map((proposal) => (
          <ProposalRow
            key={proposal.id}
            noteId={note.id}
            patientId={note.patient_id}
            proposal={proposal}
            readOnly={readOnly}
          />
        ))}
      </ul>
    </section>
  )
}
