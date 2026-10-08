// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One chart update a note proposes: the field, what the chart says now, the
 * proposed text with the change highlighted, what changed, and the lines of
 * the visit it came from. Accept writes the proposed text to the chart; Edit
 * writes the clinician's text instead; Discard writes nothing and is not
 * offered again on this note. The note itself never changes.
 */

"use client"

import { useState } from "react"
import { Check } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { useDecideChartProposal } from "@/hooks/useChartProposals"
import type { ChartProposal, DecideChartProposalRequest } from "@/types/chartProposals"
import { changeParts } from "./changeHighlight"

const DECIDED: Record<string, string> = {
  accepted: "Added to the chart",
  edited: "Added to the chart with your changes",
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

export function ProposalRow({
  noteId,
  patientId,
  proposal,
  readOnly = false,
}: {
  noteId: string
  patientId: string
  proposal: ChartProposal
  readOnly?: boolean
}) {
  const decide = useDecideChartProposal(patientId)
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(proposal.proposed_text)
  const [failed, setFailed] = useState(false)
  const pending = proposal.decision === "pending"

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
      className="space-y-2 rounded-md border border-neutral-200 p-3"
      data-testid="chart-proposal"
      aria-label={proposal.label}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="text-sm font-semibold text-neutral-900">{proposal.label}</h4>
        <span className="text-xs text-neutral-600">{proposal.what_changed}</span>
      </div>

      {pending && (
        <div className="text-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-neutral-500">
            On the chart now
          </p>
          <p className="whitespace-pre-wrap text-neutral-700">
            {proposal.current_text ?? "Not recorded"}
          </p>
        </div>
      )}

      <div>
        <p className="text-xs font-medium uppercase tracking-wide text-neutral-500">
          {pending ? "Proposed" : "From this note"}
        </p>
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
      </div>

      {proposal.evidence.length > 0 && (
        <blockquote className="border-l-2 border-neutral-300 pl-3 text-sm text-neutral-600">
          {proposal.evidence.map((e) => (
            <p key={e.segment_id}>{e.text}</p>
          ))}
        </blockquote>
      )}

      {failed && (
        <p role="alert" className="text-sm text-red-700">
          That didn&apos;t save. Try again.
        </p>
      )}

      {!pending ? (
        <p className="inline-flex items-center gap-1 text-xs text-neutral-600">
          {proposal.decision !== "discarded" && <Check className="h-3.5 w-3.5" aria-hidden />}
          {DECIDED[proposal.decision]}
        </p>
      ) : readOnly ? null : editing ? (
        <div className="flex flex-wrap justify-end gap-2">
          <Button type="button" variant="outline" size="sm" onClick={() => setEditing(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={decide.isPending || !text.trim()}
            onClick={() => send({ decision: "edit", text })}
          >
            Save to chart
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={decide.isPending}
            onClick={() => send({ decision: "discard" })}
          >
            Discard
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={decide.isPending}
            onClick={() => setEditing(true)}
          >
            Edit
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={decide.isPending}
            onClick={() => send({ decision: "accept" })}
          >
            Accept
          </Button>
        </div>
      )}
    </li>
  )
}
