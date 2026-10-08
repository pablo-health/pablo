// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Said when the check for chart updates failed, so an empty list is never
 * read as "nothing changed". Retry runs the check again where the note's
 * transcript is kept (a session's note); signing does not wait on it.
 */

"use client"

import { Button } from "@/components/ui/button"
import { useRetryChartProposals } from "@/hooks/useChartProposals"
import type { ProposalRun } from "@/types/chartProposals"

export function isNotChecked(run: ProposalRun | null | undefined): boolean {
  return run?.status === "failed"
}

export function NotChecked({
  noteId,
  run,
  readOnly = false,
}: {
  noteId: string
  run: ProposalRun
  readOnly?: boolean
}) {
  const retry = useRetryChartProposals()
  return (
    <div
      role="status"
      className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-neutral-800"
      data-testid="chart-not-checked"
    >
      <span>
        Pablo couldn&apos;t check this note for chart updates.
        {retry.isError && " That didn't work. Try again."}
      </span>
      {run.retryable && !readOnly && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={retry.isPending}
          onClick={() => retry.mutate({ noteId })}
        >
          {retry.isPending ? "Checking…" : "Retry"}
        </Button>
      )}
    </div>
  )
}
