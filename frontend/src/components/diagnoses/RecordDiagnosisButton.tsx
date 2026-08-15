// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * RecordDiagnosisButton
 *
 * Patient-scoped entry point for recording a structured diagnostic
 * determination. The clinician picks a definition (e.g. MDD, GAD), optionally
 * documents which criteria and gates are met, sees a live "meets criteria?"
 * determination, and confirms an ICD-10-CM code from the definition's options.
 * POSTs with `source: "manual"`.
 *
 * Two depths, controlled by `prominence` (defaults to "lite"; configurable per
 * deployment — e.g. lead with structured criteria for prescriber workflows):
 *  - "lite"  — the criterion checklist starts collapsed. A clinician who just
 *              needs a diagnosis + billing code on the chart picks the code and
 *              saves; the structured criteria stay one click away.
 *  - "full"  — the checklist starts expanded with the determination panel up
 *              front, for medical-model documentation.
 *
 * Determination is computed live for preview only — the backend recomputes
 * authoritatively on save (see `lib/diagnostics/evaluate`).
 */

"use client"

import { useState } from "react"
import { Check, ChevronDown, Info, Plus, Stethoscope } from "lucide-react"
import { ApiError } from "@/lib/api/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { useToast } from "@/components/ui/Toast"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import { useCreateDiagnosis, useDiagnosticDefinitions } from "@/hooks/useDiagnoses"
import { evaluateDefinition } from "@/lib/diagnostics/evaluate"
import type { DiagnosticDefinition } from "@/types/diagnoses"

export type DiagnosisFormProminence = "lite" | "full"

interface RecordDiagnosisButtonProps {
  patientId: string
  /** Default checklist depth; defaults to "lite". A deployment may set "full"
   * to lead with the structured criteria. */
  prominence?: DiagnosisFormProminence
}

/** "YYYY-MM-DDTHH:mm" for a datetime-local default of "now". */
function nowLocalInput(): string {
  const now = new Date()
  const offsetMs = now.getTimezoneOffset() * 60_000
  return new Date(now.getTime() - offsetMs).toISOString().slice(0, 16)
}

/** Map a known backend error code to a clinician-facing message. */
function messageForError(err: unknown): string {
  if (err instanceof ApiError) {
    switch (err.code) {
      case "UNKNOWN_DEFINITION":
        return "That diagnosis isn't recognized. Pick one from the list."
      case "INVALID_RESPONSES":
        return "One or more criterion responses weren't recognized."
      case "INVALID_CODE":
        return "That ICD-10 code isn't an option for this diagnosis."
      case "NOT_FOUND":
        return "You don't have access to this patient."
      default:
        return err.message
    }
  }
  return "Could not save the diagnosis. Please try again."
}

export function RecordDiagnosisButton({
  patientId,
  prominence = "lite",
}: RecordDiagnosisButtonProps) {
  const [open, setOpen] = useState(false)
  const { readOnly } = useReadOnlyMode()

  if (readOnly) return null

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">
          <Plus className="mr-2 h-4 w-4" />
          Record diagnosis
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        {/* Remount the form each open so its state resets cleanly. */}
        {open && (
          <DiagnosisForm
            patientId={patientId}
            prominence={prominence}
            onClose={() => setOpen(false)}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

interface DiagnosisFormProps {
  patientId: string
  prominence: DiagnosisFormProminence
  onClose: () => void
}

function DiagnosisForm({ patientId, prominence, onClose }: DiagnosisFormProps) {
  const { data, isLoading } = useDiagnosticDefinitions()
  const definitions = data?.data ?? []

  const [code, setCode] = useState<string | null>(null)
  const [criterionResponses, setCriterionResponses] = useState<
    Record<string, boolean>
  >({})
  const [gateResponses, setGateResponses] = useState<Record<string, boolean>>(
    {},
  )
  // undefined = untouched (defer to the suggested code); null = explicit none.
  const [chosenIcd10, setChosenIcd10] = useState<string | null | undefined>(
    undefined,
  )
  const [assessedAt, setAssessedAt] = useState(nowLocalInput)
  const [criteriaOpen, setCriteriaOpen] = useState(prominence === "full")

  const { showToast } = useToast()
  const createDiagnosis = useCreateDiagnosis()

  // The active definition: the chosen one, else the first available.
  const definition: DiagnosticDefinition | undefined =
    definitions.find((d) => d.code === code) ?? definitions[0]

  function selectDefinition(next: string) {
    setCode(next)
    setCriterionResponses({})
    setGateResponses({})
    setChosenIcd10(undefined)
  }

  if (isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-24 w-full" />
      </div>
    )
  }

  if (!definition) {
    return (
      <div className="py-6 text-center text-sm text-neutral-600">
        No diagnostic definitions are available.
      </div>
    )
  }

  const outcome = evaluateDefinition(
    definition,
    criterionResponses,
    gateResponses,
  )

  // Effective code: an explicit choice wins; otherwise default to the engine's
  // suggestion once criteria are met.
  const effectiveIcd10 =
    chosenIcd10 === undefined ? outcome.suggestedIcd10 : chosenIcd10

  const canSubmit =
    !createDiagnosis.isPending &&
    (effectiveIcd10 != null || outcome.meetsCriteria === true)

  function toggleCriterion(key: string) {
    setCriterionResponses((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  function toggleGate(key: string) {
    setGateResponses((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  async function handleSubmit() {
    if (!canSubmit || !definition) return
    try {
      await createDiagnosis.mutateAsync({
        patientId,
        data: {
          instrument: definition.code,
          source: "manual",
          assessed_at: new Date(assessedAt).toISOString(),
          criterion_responses: criterionResponses,
          gate_responses: gateResponses,
          determined_icd10: effectiveIcd10,
        },
      })
      showToast("Diagnosis recorded.", "success")
      onClose()
    } catch (err) {
      showToast(messageForError(err), "error")
    }
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>
          {prominence === "full" ? "Document diagnostic criteria" : "Record diagnosis"}
        </DialogTitle>
        <DialogDescription>
          Pick a diagnosis and confirm an ICD-10-CM code. Document the criteria
          to record how the determination was reached.
        </DialogDescription>
      </DialogHeader>

      <div className="space-y-5 pt-1">
        {/* The bundled criterion wording is a draft pending clinical review —
            surface that so it isn't mistaken for validated content. */}
        <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 p-2.5 text-xs text-amber-800">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            Draft criteria — the bundled wording is for clinical review before
            clinical use and isn&apos;t a substitute for clinical judgment.
            Confirm against the source criteria before recording.
          </span>
        </div>

        {/* Diagnosis picker */}
        <div className="space-y-1.5">
          <Label>Diagnosis</Label>
          <div className="flex flex-wrap gap-2">
            {definitions.map((d) => (
              <button
                key={d.code}
                type="button"
                onClick={() => selectDefinition(d.code)}
                className={
                  d.code === definition.code
                    ? "rounded-md border border-primary-400 bg-primary-50 px-3 py-1.5 text-sm font-medium text-primary-800"
                    : "rounded-md border border-neutral-200 px-3 py-1.5 text-sm text-neutral-700 hover:border-neutral-300"
                }
              >
                {d.display_name}
              </button>
            ))}
          </div>
        </div>

        {/* Date assessed */}
        <div className="space-y-1.5">
          <Label htmlFor="assessed-at">Date assessed</Label>
          <Input
            id="assessed-at"
            type="datetime-local"
            value={assessedAt}
            onChange={(e) => setAssessedAt(e.target.value)}
            className="w-fit"
          />
        </div>

        {/* Criterion checklist (collapsible) */}
        <div className="rounded-md border border-neutral-200">
          <button
            type="button"
            onClick={() => setCriteriaOpen((v) => !v)}
            className="flex w-full items-center justify-between px-3 py-2 text-sm font-medium text-neutral-800"
            aria-expanded={criteriaOpen}
          >
            <span>Document criteria{prominence === "lite" ? " (optional)" : ""}</span>
            <ChevronDown
              className={`h-4 w-4 text-neutral-500 transition-transform ${
                criteriaOpen ? "rotate-180" : ""
              }`}
            />
          </button>

          {criteriaOpen && (
            <div className="space-y-4 border-t border-neutral-100 px-3 py-3">
              {definition.criterion_groups.map((group) => {
                const metCount = group.criteria.filter(
                  (c) => criterionResponses[c.key] === true,
                ).length
                return (
                  <div key={group.key} className="space-y-2">
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-medium text-neutral-800">
                        {group.label}
                      </p>
                      <span className="text-xs text-neutral-500">
                        {metCount}/{group.min_met} needed
                      </span>
                    </div>
                    <div className="space-y-1.5">
                      {group.criteria.map((c) => {
                        const checked = criterionResponses[c.key] === true
                        return (
                          <button
                            key={c.key}
                            type="button"
                            aria-pressed={checked}
                            onClick={() => toggleCriterion(c.key)}
                            className={`flex w-full items-start gap-2 rounded-md border px-2.5 py-1.5 text-left text-sm ${
                              checked
                                ? "border-primary-300 bg-primary-50 text-primary-900"
                                : "border-neutral-200 text-neutral-700 hover:border-neutral-300"
                            }`}
                          >
                            <span
                              className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                                checked
                                  ? "border-primary-500 bg-primary-500 text-white"
                                  : "border-neutral-300"
                              }`}
                            >
                              {checked && <Check className="h-3 w-3" />}
                            </span>
                            <span>
                              {c.label}
                              {c.cardinal && (
                                <span className="ml-1 text-xs text-neutral-400">
                                  (core)
                                </span>
                              )}
                            </span>
                          </button>
                        )
                      })}
                    </div>
                  </div>
                )
              })}

              {definition.gates.length > 0 && (
                <div className="space-y-1.5 border-t border-neutral-100 pt-3">
                  <p className="text-sm font-medium text-neutral-800">
                    Clinical gates
                  </p>
                  {definition.gates.map((gate) => {
                    const checked = gateResponses[gate.key] === true
                    return (
                      <button
                        key={gate.key}
                        type="button"
                        aria-pressed={checked}
                        onClick={() => toggleGate(gate.key)}
                        className={`flex w-full items-start gap-2 rounded-md border px-2.5 py-1.5 text-left text-sm ${
                          checked
                            ? "border-primary-300 bg-primary-50 text-primary-900"
                            : "border-neutral-200 text-neutral-700 hover:border-neutral-300"
                        }`}
                      >
                        <span
                          className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
                            checked
                              ? "border-primary-500 bg-primary-500 text-white"
                              : "border-neutral-300"
                          }`}
                        >
                          {checked && <Check className="h-3 w-3" />}
                        </span>
                        <span>{gate.label}</span>
                      </button>
                    )
                  })}
                </div>
              )}

              {/* Live determination — hidden for checklist (no verdict). */}
              {outcome.meetsCriteria !== null && (
                <DeterminationPanel outcome={outcome} />
              )}
            </div>
          )}
        </div>

        {/* ICD-10 confirmation */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <Label>ICD-10-CM code</Label>
            {outcome.suggestedIcd10 && (
              <span className="text-xs text-emerald-700">
                Suggested: {outcome.suggestedIcd10}
              </span>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {definition.icd10_options.map((opt) => {
              const active = effectiveIcd10 === opt.code
              return (
                <button
                  key={opt.code}
                  type="button"
                  onClick={() => setChosenIcd10(opt.code)}
                  className={
                    active
                      ? "rounded-md border border-primary-400 bg-primary-50 px-2.5 py-1.5 text-sm font-medium text-primary-800"
                      : "rounded-md border border-neutral-200 px-2.5 py-1.5 text-sm text-neutral-700 hover:border-neutral-300"
                  }
                  title={opt.label}
                >
                  {opt.code}
                </button>
              )
            })}
            <button
              type="button"
              onClick={() => setChosenIcd10(null)}
              className={
                effectiveIcd10 === null
                  ? "rounded-md border border-neutral-400 bg-neutral-100 px-2.5 py-1.5 text-sm font-medium text-neutral-700"
                  : "rounded-md border border-neutral-200 px-2.5 py-1.5 text-sm text-neutral-500 hover:border-neutral-300"
              }
            >
              No code
            </button>
          </div>
          {effectiveIcd10 && (
            <p className="text-xs text-neutral-500">
              {definition.icd10_options.find((o) => o.code === effectiveIcd10)
                ?.label ?? ""}
            </p>
          )}
        </div>
      </div>

      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button onClick={handleSubmit} disabled={!canSubmit}>
          {createDiagnosis.isPending ? "Saving…" : "Save diagnosis"}
        </Button>
      </DialogFooter>
    </>
  )
}

function DeterminationPanel({
  outcome,
}: {
  outcome: ReturnType<typeof evaluateDefinition>
}) {
  if (outcome.meetsCriteria === true) {
    return (
      <div className="flex items-start gap-2 rounded-md border border-emerald-200 bg-emerald-50 p-3">
        <Check className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
        <p className="text-sm font-medium text-emerald-800">
          Criteria met
          {outcome.suggestedIcd10 ? ` — ${outcome.suggestedIcd10}` : ""}
        </p>
      </div>
    )
  }
  return (
    <div className="space-y-1 rounded-md border border-neutral-200 bg-neutral-50 p-3">
      <p className="text-sm font-medium text-neutral-700">Criteria not yet met</p>
      <ul className="list-inside list-disc text-xs text-neutral-600">
        {outcome.unmetReasons.map((reason) => (
          <li key={reason}>{reason}</li>
        ))}
      </ul>
    </div>
  )
}
