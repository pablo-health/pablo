// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A note's chart updates as a ledger: grouped under the chart section they
 * belong to, each row a field and the text proposed for it, decided one at a
 * time or a section at once. The same ledger serves the sign step, a signed
 * or imported note's page, and the client's chart.
 *
 * "Accept all" on a section accepts what can be accepted without reading
 * further: a medication whose name the source may have misheard is left for
 * the clinician to confirm on its own row.
 */

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { useDecideChartProposal } from "@/hooks/useChartProposals"
import type { ChartProposal } from "@/types/chartProposals"
import { ProposalRow, type EvidenceHandler } from "./ProposalRow"

/**
 * A proposal's label reads "<section>: <field>" ("Social history and
 * supports: Supports", "Medications: Start buspirone"); a section with one
 * field carries only the section ("Trauma history").
 */
export function splitLabel(label: string): { section: string; field: string } {
  const at = label.indexOf(": ")
  if (at < 0) return { section: label, field: label }
  return { section: label.slice(0, at), field: label.slice(at + 2) }
}

interface Section {
  name: string
  proposals: ChartProposal[]
}

export function groupBySection(proposals: ChartProposal[]): Section[] {
  const sections = new Map<string, ChartProposal[]>()
  for (const proposal of proposals) {
    const { section } = splitLabel(proposal.label)
    const list = sections.get(section)
    if (list) list.push(proposal)
    else sections.set(section, [proposal])
  }
  return [...sections].map(([name, list]) => ({ name, proposals: list }))
}

/** Pending, and acceptable without reading the source first. */
function acceptableAtOnce(proposal: ChartProposal): boolean {
  return proposal.decision === "pending" && !proposal.change?.name_uncertain
}

export interface ProposalLedgerProps {
  noteId: string
  patientId: string
  proposals: ChartProposal[]
  readOnly?: boolean
  /** Lights the cited lines in the source pane; omitted where no pane is shown. */
  onEvidenceClick?: EvidenceHandler
}

export function ProposalLedger({
  noteId,
  patientId,
  proposals,
  readOnly = false,
  onEvidenceClick,
}: ProposalLedgerProps) {
  const sections = groupBySection(proposals)
  return (
    <div className="space-y-4" data-testid="proposal-ledger">
      {sections.map((section) => (
        <LedgerSection
          key={section.name}
          noteId={noteId}
          patientId={patientId}
          section={section}
          readOnly={readOnly}
          onEvidenceClick={onEvidenceClick}
        />
      ))}
    </div>
  )
}

function LedgerSection({
  noteId,
  patientId,
  section,
  readOnly,
  onEvidenceClick,
}: {
  noteId: string
  patientId: string
  section: Section
  readOnly: boolean
  onEvidenceClick?: EvidenceHandler
}) {
  const decide = useDecideChartProposal(patientId)
  const [acceptingAll, setAcceptingAll] = useState(false)
  const [failed, setFailed] = useState(false)
  const atOnce = section.proposals.filter(acceptableAtOnce)

  const acceptAll = async () => {
    setAcceptingAll(true)
    setFailed(false)
    try {
      for (const proposal of atOnce) {
        await decide.mutateAsync({ noteId, proposalId: proposal.id, data: { decision: "accept" } })
      }
    } catch {
      setFailed(true)
    } finally {
      setAcceptingAll(false)
    }
  }

  return (
    <section aria-label={section.name} className="space-y-2">
      <div className="flex items-baseline justify-between gap-3 border-b border-neutral-200 pb-1">
        <h4 className="text-sm font-semibold text-neutral-800">{section.name}</h4>
        {!readOnly && atOnce.length > 1 && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={acceptingAll}
            onClick={acceptAll}
            aria-label={`Accept all ${section.name}`}
          >
            {acceptingAll ? "Accepting…" : `Accept all ${atOnce.length}`}
          </Button>
        )}
      </div>
      {failed && (
        <p role="alert" className="text-sm text-red-700">
          Not every update saved. The rest are still here to accept.
        </p>
      )}
      <ul className="divide-y divide-neutral-100">
        {section.proposals.map((proposal) => (
          <ProposalRow
            key={proposal.id}
            noteId={noteId}
            patientId={patientId}
            proposal={proposal}
            readOnly={readOnly}
            busy={acceptingAll}
            onEvidenceClick={onEvidenceClick}
          />
        ))}
      </ul>
    </section>
  )
}
