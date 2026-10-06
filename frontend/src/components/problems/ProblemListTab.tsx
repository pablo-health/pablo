// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ProblemListTab
 *
 * The chart's problem list: the client's diagnoses, with codes where the
 * clinician has them. The first active entry is the primary diagnosis, so
 * order matters and can be changed. Resolving keeps an entry as history;
 * removing is for one entered in error. Notes are drafted against this list,
 * and a visit's diagnosis codes start from its active entries.
 */

"use client"

import { ArrowDown, ArrowUp, ListChecks, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { useToast } from "@/components/ui/Toast"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import {
  usePatientProblems,
  useRemoveProblem,
  useReorderProblems,
  useUpdateProblem,
} from "@/hooks/useProblems"
import type { Problem, ProblemStatus } from "@/types/problems"
import { AddProblemForm, STATUS_LABEL } from "./AddProblemForm"

const STATUS_BADGE: Record<ProblemStatus, string> = {
  active: "bg-green-100 text-green-800",
  rule_out: "bg-yellow-100 text-yellow-800",
  resolved: "bg-neutral-100 text-neutral-600",
}

export function ProblemListTab({ patientId }: { patientId: string }) {
  const { data, isLoading, error } = usePatientProblems(patientId)
  const updateProblem = useUpdateProblem()
  const reorderProblems = useReorderProblems()
  const removeProblem = useRemoveProblem()
  const { showToast } = useToast()
  const { readOnly } = useReadOnlyMode()

  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    )
  }

  if (error) {
    return (
      <p className="text-sm text-red-500">
        {error instanceof Error ? error.message : "Failed to load the problem list."}
      </p>
    )
  }

  const problems = data?.data ?? []

  async function setStatus(problem: Problem, status: ProblemStatus) {
    try {
      await updateProblem.mutateAsync({ patientId, problemId: problem.id, data: { status } })
    } catch {
      showToast("Could not update the diagnosis. Please try again.", "error")
    }
  }

  async function move(index: number, offset: -1 | 1) {
    const order = problems.map((p) => p.id)
    const [moved] = order.splice(index, 1)
    order.splice(index + offset, 0, moved)
    try {
      await reorderProblems.mutateAsync({ patientId, problemIds: order })
    } catch {
      showToast("Could not reorder the list. Please try again.", "error")
    }
  }

  async function remove(problem: Problem) {
    if (
      typeof window !== "undefined" &&
      !window.confirm(`Remove ${problem.label} from the problem list?`)
    ) {
      return
    }
    try {
      await removeProblem.mutateAsync({ patientId, problemId: problem.id })
    } catch {
      showToast("Could not remove the diagnosis. Please try again.", "error")
    }
  }

  // Order moves stay within a status: the list always reads active, then
  // rule-out, then resolved.
  const canMove = (index: number, offset: -1 | 1) =>
    problems[index + offset]?.status === problems[index].status

  return (
    <div className="space-y-4">
      {problems.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-6 text-center">
          <ListChecks className="h-8 w-8 text-neutral-300" />
          <p className="text-sm text-neutral-600">No diagnoses recorded.</p>
        </div>
      ) : (
        <ul className="space-y-2" aria-label="Problem list">
          {problems.map((problem, index) => (
            <li
              key={problem.id}
              data-testid="problem-row"
              className="flex items-center justify-between gap-3 rounded-lg border border-neutral-100 px-3 py-2.5"
            >
              <span className="flex min-w-0 items-center gap-2">
                <span className="truncate text-sm font-medium text-neutral-900">
                  {problem.label}
                </span>
                <span className="shrink-0 text-xs text-neutral-500">
                  {problem.icd10_code ?? "No code"}
                </span>
              </span>
              <span className="flex shrink-0 items-center gap-1">
                <span
                  className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${STATUS_BADGE[problem.status]}`}
                >
                  {STATUS_LABEL[problem.status]}
                </span>
                {!readOnly && (
                  <>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => move(index, -1)}
                      disabled={!canMove(index, -1) || reorderProblems.isPending}
                      aria-label={`Move ${problem.label} up`}
                    >
                      <ArrowUp className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => move(index, 1)}
                      disabled={!canMove(index, 1) || reorderProblems.isPending}
                      aria-label={`Move ${problem.label} down`}
                    >
                      <ArrowDown className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() =>
                        setStatus(problem, problem.status === "resolved" ? "active" : "resolved")
                      }
                      disabled={updateProblem.isPending}
                    >
                      {problem.status === "resolved" ? "Reactivate" : "Resolve"}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => remove(problem)}
                      disabled={removeProblem.isPending}
                      aria-label={`Remove ${problem.label}`}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
      {!readOnly && <AddProblemForm patientId={patientId} />}
    </div>
  )
}
