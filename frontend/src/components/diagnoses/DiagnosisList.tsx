// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * DiagnosisList
 *
 * Presentational list of a patient's recorded diagnostic determinations, most
 * recent first. Each row shows the diagnosis label, the clinician-confirmed
 * ICD-10-CM code, whether the documented criteria were met, and the assessment
 * date, with a delete affordance.
 */

"use client"

import { Trash2 } from "lucide-react"
import type { DiagnosticAssessment } from "@/types/diagnoses"

interface DiagnosisListProps {
  assessments: DiagnosticAssessment[]
  onDelete: (assessment: DiagnosticAssessment) => void
  deletingId?: string | null
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      })
}

export function DiagnosisList({
  assessments,
  onDelete,
  deletingId,
}: DiagnosisListProps) {
  const ordered = [...assessments].sort((a, b) =>
    b.assessed_at.localeCompare(a.assessed_at),
  )

  return (
    <ul className="divide-y divide-neutral-100">
      {ordered.map((a) => (
        <li
          key={a.id}
          className="flex items-center justify-between gap-3 py-2.5 text-sm"
        >
          <div className="flex min-w-0 flex-col">
            <span className="truncate font-medium text-neutral-900">
              {a.diagnosis_label ?? a.instrument}
            </span>
            <span className="text-xs text-neutral-500">
              {formatDate(a.assessed_at)}
            </span>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {a.determined_icd10 ? (
              <span className="rounded bg-neutral-100 px-2 py-0.5 font-mono text-xs font-medium text-neutral-800">
                {a.determined_icd10}
              </span>
            ) : (
              <span className="text-xs text-neutral-400">no code</span>
            )}
            {/* Checklist assessments (meets_criteria === null) make no
                algorithmic determination, so they carry no verdict badge —
                null and false are distinct states, not both "Impression". */}
            {a.meets_criteria !== null && (
              <span
                className={
                  a.meets_criteria
                    ? "rounded px-2 py-0.5 text-xs font-medium text-emerald-700"
                    : "rounded px-2 py-0.5 text-xs font-medium text-neutral-500"
                }
              >
                {a.meets_criteria ? "Criteria met" : "Impression"}
              </span>
            )}
            <button
              type="button"
              onClick={() => onDelete(a)}
              disabled={deletingId === a.id}
              aria-label="Delete diagnosis"
              className="text-neutral-400 transition-colors hover:text-red-500 disabled:opacity-40"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        </li>
      ))}
    </ul>
  )
}
