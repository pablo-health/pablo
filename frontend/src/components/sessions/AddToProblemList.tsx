// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Carry a diagnosis the clinician stated in a note onto the chart's problem
 * list, one at a time and only when asked. The problem list is the chart's
 * record of diagnoses; a note only records what was said, so nothing moves
 * between them on its own.
 */

"use client"

import { useState } from "react"
import { Check } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useAddProblem, usePatientProblems } from "@/hooks/useProblems"
import { ApiError } from "@/lib/api/client"
import type { StatedDiagnosis } from "@/lib/statedDiagnoses"
import type { Problem, ProblemStatus } from "@/types/problems"
import type { DiagnosisAction } from "./DiagnosesField"

/** A stated status as the problem list records it: rule-out, or active. */
export function problemStatusFor(status: string | null): ProblemStatus {
  const words = (status ?? "").toLowerCase().replace(/[^a-z]/g, "")
  return words.includes("ruleout") ? "rule_out" : "active"
}

/**
 * Whether the list already holds this diagnosis, the way the server decides
 * it: the same code, or, when neither has a code, the same label.
 */
export function isListed(dx: StatedDiagnosis, problems: Problem[]): boolean {
  const code = dx.code?.trim().toUpperCase()
  return problems.some((p) =>
    code || p.icd10_code
      ? !!code && p.icd10_code?.toUpperCase() === code
      : p.label.trim().toLowerCase() === dx.label.trim().toLowerCase(),
  )
}

type Outcome = "added" | "bad_code" | "failed"

function AddDiagnosisButton({
  patientId,
  noteId,
  dx,
}: {
  patientId: string
  noteId: string
  dx: StatedDiagnosis
}) {
  // One request per client, shared by every diagnosis on the page; it runs
  // only when a note actually shows one.
  const { data: problems } = usePatientProblems(patientId)
  const addProblem = useAddProblem()
  const [outcome, setOutcome] = useState<Outcome | null>(null)

  if (outcome === "added" || isListed(dx, problems?.data ?? [])) {
    return (
      <span className="inline-flex shrink-0 items-center gap-1 text-xs text-neutral-600">
        <Check className="h-3.5 w-3.5" aria-hidden />
        On problem list
      </span>
    )
  }

  const add = async () => {
    try {
      await addProblem.mutateAsync({
        patientId,
        data: {
          label: dx.label,
          icd10_code: dx.code,
          status: problemStatusFor(dx.status),
          source_note_id: noteId,
        },
      })
      setOutcome("added")
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setOutcome("added")
      else if (err instanceof ApiError && err.status === 422) setOutcome("bad_code")
      else setOutcome("failed")
    }
  }

  return (
    <span className="inline-flex shrink-0 items-center gap-2">
      {outcome === "bad_code" && (
        <span role="alert" className="text-xs text-red-700">
          Not an ICD-10 code. Correct it in the note first.
        </span>
      )}
      {outcome === "failed" && (
        <span role="alert" className="text-xs text-red-700">
          Couldn&apos;t add it.
        </span>
      )}
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={addProblem.isPending}
        aria-label={`Add ${dx.label} to problem list`}
        onClick={add}
      >
        {addProblem.isPending ? "Adding…" : "Add to problem list"}
      </Button>
    </span>
  )
}

/** The action a client's note offers beside each stated diagnosis. */
export function addToProblemListAction(patientId: string, noteId: string): DiagnosisAction {
  function AddAction(dx: StatedDiagnosis) {
    return <AddDiagnosisButton patientId={patientId} noteId={noteId} dx={dx} />
  }
  return AddAction
}
