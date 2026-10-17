// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One chart update a note proposes: the field, the proposed text with the
 * change highlighted, what the chart says now when it says something, and
 * the lines of the source it came from, each a mark that lights that line in
 * the source pane. Accept writes the proposed text to the chart; Edit writes
 * the clinician's text instead; Discard writes nothing and is not offered
 * again on this note. The note itself never changes.
 *
 * A medication change (start, stop, change, add) is a row against the
 * current list: accepted or discarded, never rewritten here, since it writes
 * a structured medication record. The list itself is edited on the client's
 * page.
 */

"use client"

import { useState } from "react"
import { Check } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { useDecideChartProposal } from "@/hooks/useChartProposals"
import { cn } from "@/lib/utils"
import type {
  ChartProposal,
  DecideChartProposalRequest,
  ProposalEvidence,
} from "@/types/chartProposals"
import { changeParts } from "./changeHighlight"
import { splitLabel } from "./ProposalLedger"

/** Called with the cited segment ids when a source mark is clicked. */
export type EvidenceHandler = (segmentIds: number[]) => void

const DECIDED: Record<string, string> = {
  accepted: "Added to the chart",
  edited: "Added to the chart with your changes",
  discarded: "Discarded",
}

const MEDICATION_DECIDED: Record<string, string> = {
  accepted: "Medication list updated",
  discarded: "Discarded",
}

function ProposedText({ current, proposed }: { current: string | null; proposed: string }) {
  const { before, changed, after } = changeParts(current, proposed)
  return (
    <p className="whitespace-pre-wrap text-sm text-neutral-900" data-testid="proposed-text">
      {before}
      {changed && <mark className="rounded bg-secondary-100 px-0.5 text-neutral-900">{changed}</mark>}
      {after}
    </p>
  )
}

/** The paragraph or line of the source a proposal cites, numbered from one as the pane shows it. */
export function SourceMark({
  evidence,
  onClick,
}: {
  evidence: ProposalEvidence
  onClick?: EvidenceHandler
}) {
  const label = `¶${evidence.segment_id + 1}`
  const className =
    "inline-flex shrink-0 items-center rounded border border-neutral-300 bg-white px-1 font-mono text-[11px] leading-5 text-neutral-600"
  if (!onClick) {
    return <span className={className}>{label}</span>
  }
  return (
    <button
      type="button"
      className={cn(className, "hover:border-primary-400 hover:text-primary-700")}
      title="Show where the source says so"
      aria-label={`Show source paragraph ${evidence.segment_id + 1}`}
      onClick={() => onClick([evidence.segment_id])}
    >
      {label}
    </button>
  )
}

export function ProposalRow({
  noteId,
  patientId,
  proposal,
  readOnly = false,
  busy = false,
  onEvidenceClick,
}: {
  noteId: string
  patientId: string
  proposal: ChartProposal
  readOnly?: boolean
  /** The section is being accepted at once; the row's own buttons wait. */
  busy?: boolean
  onEvidenceClick?: EvidenceHandler
}) {
  const decide = useDecideChartProposal(patientId)
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(proposal.proposed_text)
  const [failed, setFailed] = useState(false)
  const pending = proposal.decision === "pending"
  const decided = proposal.change ? MEDICATION_DECIDED : DECIDED
  const { field } = splitLabel(proposal.label)
  const disabled = decide.isPending || busy

  const send = async (data: DecideChartProposalRequest) => {
    setFailed(false)
    try {
      await decide.mutateAsync({ noteId, proposalId: proposal.id, data })
      setEditing(false)
    } catch {
      setFailed(true)
    }
  }

  return (
    <li
      className={cn("grid gap-x-4 gap-y-1 py-3 sm:grid-cols-[9rem_1fr]", !pending && "opacity-80")}
      data-testid="chart-proposal"
      aria-label={proposal.label}
    >
      <div className="min-w-0">
        <h5 className="text-sm font-medium text-neutral-900">{field}</h5>
        <p className="text-xs text-neutral-500">{proposal.what_changed}</p>
      </div>

      <div className="min-w-0 space-y-1.5">
        {editing ? (
          <Textarea
            aria-label={`Edit ${proposal.label}`}
            value={text}
            rows={4}
            onChange={(e) => setText(e.target.value)}
          />
        ) : (
          <ProposedText
            current={pending ? proposal.current_text : null}
            proposed={proposal.decided_text ?? proposal.proposed_text}
          />
        )}

        {pending && (
          <p className="text-xs text-neutral-500">
            <span className="text-neutral-400">On the chart now</span>{" "}
            <span className="whitespace-pre-wrap text-neutral-600">
              {proposal.current_text ?? (proposal.change ? "Not on the list" : "Not recorded")}
            </span>
          </p>
        )}

        {proposal.evidence.length > 0 && (
          <blockquote className="space-y-0.5 text-xs text-neutral-600">
            {proposal.evidence.map((e) => (
              <p key={e.segment_id} className="flex items-start gap-1.5">
                <SourceMark evidence={e} onClick={onEvidenceClick} />
                <span className="min-w-0">{e.text}</span>
              </p>
            ))}
          </blockquote>
        )}

        {proposal.change?.name_uncertain && (
          <p role="alert" className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">
            Medication name uncertain. Heard as &ldquo;{proposal.change.heard_as}&rdquo;. Check the
            source before accepting.
          </p>
        )}

        {failed && (
          <p role="alert" className="text-sm text-red-700">
            That didn&apos;t save. Try again.
          </p>
        )}

        {!pending ? (
          <p className="inline-flex items-center gap-1 text-xs text-neutral-600">
            {proposal.decision !== "discarded" && <Check className="h-3.5 w-3.5" aria-hidden />}
            {decided[proposal.decision]}
          </p>
        ) : readOnly ? null : editing ? (
          <div className="flex flex-wrap justify-end gap-2 pt-1">
            <Button type="button" variant="outline" size="sm" onClick={() => setEditing(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={disabled || !text.trim()}
              onClick={() => send({ decision: "edit", text })}
            >
              Save to chart
            </Button>
          </div>
        ) : (
          <div className="flex flex-wrap justify-end gap-1 pt-1">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={disabled}
              onClick={() => send({ decision: "discard" })}
            >
              Discard
            </Button>
            {proposal.editable && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={disabled}
                onClick={() => setEditing(true)}
              >
                Edit
              </Button>
            )}
            <Button
              type="button"
              size="sm"
              disabled={disabled}
              onClick={() => send({ decision: "accept" })}
            >
              {proposal.change?.name_uncertain ? "Confirm and accept" : "Accept"}
            </Button>
          </div>
        )}
      </div>
    </li>
  )
}
