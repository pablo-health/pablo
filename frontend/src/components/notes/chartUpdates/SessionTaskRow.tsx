// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Something the clinician said in the session they would do, proposed as a
 * task. The title and the due date can be changed before it is added; adding
 * files it to the clinician's own tasks, tied to this client, and dismissing
 * files nothing. Either way it is not proposed again from that line.
 *
 * The date is the one the session said, read from the words shown beside it.
 * Where no single day was said ("next week"), the words are shown and the date
 * is left for the clinician to pick, or to leave empty.
 */

"use client"

import { useState } from "react"
import { Check } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useDecideChartProposal } from "@/hooks/useChartProposals"
import { cn } from "@/lib/utils"
import type { ChartProposal } from "@/types/chartProposals"
import { SourceMark, type EvidenceHandler } from "./ProposalRow"

const DECIDED: Record<string, string> = {
  accepted: "Added to your tasks",
  edited: "Added to your tasks",
  discarded: "Dismissed",
}

export function SessionTaskRow({
  noteId,
  patientId,
  proposal,
  readOnly = false,
  onEvidenceClick,
}: {
  noteId?: string
  patientId: string
  proposal: ChartProposal
  readOnly?: boolean
  onEvidenceClick?: EvidenceHandler
}) {
  const decide = useDecideChartProposal(patientId)
  const proposedDue = proposal.commitment?.due_date ?? ""
  const [title, setTitle] = useState(proposal.proposed_text)
  const [due, setDue] = useState(proposedDue)
  const [missingTitle, setMissingTitle] = useState(false)
  const [failed, setFailed] = useState(false)
  const pending = proposal.decision === "pending"
  const said = proposal.commitment?.due_said
  const id = `session-task-${proposal.id}`

  const send = async (decision: "add" | "dismiss") => {
    setFailed(false)
    const text = title.trim()
    if (decision === "add" && !text) {
      setMissingTitle(true)
      return
    }
    try {
      await decide.mutateAsync({
        noteId,
        proposalId: proposal.id,
        data:
          decision === "dismiss"
            ? { decision: "discard" }
            : text === proposal.proposed_text
              ? { decision: "accept", due_date: due || null }
              : { decision: "edit", text, due_date: due || null },
      })
    } catch {
      setFailed(true)
    }
  }

  return (
    <li
      className={cn("space-y-1.5 py-3", !pending && "opacity-80")}
      data-testid="session-task-proposal"
      aria-label={proposal.decided_text ?? proposal.proposed_text}
    >
      {pending && !readOnly ? (
        <div className="grid gap-2 sm:grid-cols-[1fr_10rem]">
          <div className="space-y-1">
            <label htmlFor={`${id}-title`} className="sr-only">
              Task
            </label>
            <Input
              id={`${id}-title`}
              value={title}
              maxLength={255}
              aria-invalid={missingTitle || undefined}
              onChange={(e) => {
                setTitle(e.target.value)
                setMissingTitle(false)
              }}
            />
            {missingTitle && <p className="text-[11px] text-rose-700">Add a title.</p>}
          </div>
          <div>
            <label htmlFor={`${id}-due`} className="sr-only">
              Due date
            </label>
            <Input
              id={`${id}-due`}
              type="date"
              value={due}
              onChange={(e) => setDue(e.target.value)}
            />
          </div>
        </div>
      ) : (
        <p className="text-sm text-neutral-900">{proposal.decided_text ?? proposal.proposed_text}</p>
      )}

      {said && (
        <p className="text-xs text-neutral-500">
          <span className="text-neutral-400">You said</span> &ldquo;{said}&rdquo;
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
      ) : readOnly ? null : (
        <div className="flex flex-wrap justify-end gap-1 pt-1">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={decide.isPending}
            onClick={() => send("dismiss")}
          >
            Dismiss
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={decide.isPending}
            onClick={() => send("add")}
          >
            Add to my tasks
          </Button>
        </div>
      )}
    </li>
  )
}
