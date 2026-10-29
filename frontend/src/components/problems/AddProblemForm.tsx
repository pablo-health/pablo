// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * AddProblemForm
 *
 * One line to put a diagnosis on the problem list: what it is, its ICD-10
 * code if the clinician has one, and its status. The code is checked for
 * shape only — a label with no code is a complete entry.
 */

"use client"

import { useState, type FormEvent } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useToast } from "@/components/ui/Toast"
import { useAddProblem } from "@/hooks/useProblems"
import { ApiError } from "@/lib/api/client"
import { ICD10_CODE_PATTERN, type ProblemStatus } from "@/types/problems"

export const STATUS_LABEL: Record<ProblemStatus, string> = {
  active: "Active",
  rule_out: "Rule-out",
  resolved: "Resolved",
}

export function AddProblemForm({ patientId }: { patientId: string }) {
  const addProblem = useAddProblem()
  const { showToast } = useToast()
  const [label, setLabel] = useState("")
  const [code, setCode] = useState("")
  const [status, setStatus] = useState<ProblemStatus>("active")
  const [codeError, setCodeError] = useState<string | null>(null)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const typedCode = code.trim().toUpperCase()
    if (typedCode && !ICD10_CODE_PATTERN.test(typedCode)) {
      setCodeError("Enter a code like F41.1, or leave it blank.")
      return
    }
    setCodeError(null)
    try {
      await addProblem.mutateAsync({
        patientId,
        data: { label: label.trim(), icd10_code: typedCode || null, status },
      })
      setLabel("")
      setCode("")
      setStatus("active")
    } catch (err) {
      const listed = err instanceof ApiError && err.status === 409
      showToast(
        listed
          ? "That diagnosis is already on the list."
          : "Could not add the diagnosis. Please try again.",
        "error",
      )
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-wrap items-end gap-3"
      aria-label="Add a diagnosis"
    >
      <div className="flex min-w-[14rem] flex-1 flex-col gap-1">
        <Label htmlFor="problem-label">Diagnosis</Label>
        <Input
          id="problem-label"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          maxLength={255}
          required
        />
      </div>
      <div className="flex w-32 flex-col gap-1">
        <Label htmlFor="problem-code">ICD-10 code</Label>
        <Input
          id="problem-code"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder="Optional"
          aria-invalid={codeError ? true : undefined}
          aria-describedby={codeError ? "problem-code-error" : undefined}
        />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor="problem-status">Status</Label>
        <select
          id="problem-status"
          value={status}
          onChange={(e) => setStatus(e.target.value as ProblemStatus)}
          className="h-10 rounded-md border border-neutral-300 px-2 text-sm"
        >
          {(Object.keys(STATUS_LABEL) as ProblemStatus[]).map((value) => (
            <option key={value} value={value}>
              {STATUS_LABEL[value]}
            </option>
          ))}
        </select>
      </div>
      <Button type="submit" size="sm" disabled={!label.trim() || addProblem.isPending}>
        Add
      </Button>
      {codeError && (
        <p id="problem-code-error" className="w-full text-sm text-red-500">
          {codeError}
        </p>
      )}
    </form>
  )
}
