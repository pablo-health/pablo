// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * OutcomeMeasuresTab
 *
 * Chart tab listing a patient's standardized instrument scores, grouped by
 * instrument with a trend each, plus the manual-entry on-ramp. Fetches the
 * full (unfiltered) list once and groups client-side — the list endpoint
 * already returns every instrument ordered by administered_at ascending.
 */

"use client"

import { Activity } from "lucide-react"
import { Skeleton } from "@/components/ui/skeleton"
import { useToast } from "@/components/ui/Toast"
import { OutcomeMeasureTrend } from "./OutcomeMeasureTrend"
import { RecordOutcomeMeasureButton } from "./RecordOutcomeMeasureButton"
import {
  useDeleteOutcomeMeasure,
  usePatientOutcomeMeasures,
} from "@/hooks/useOutcomeMeasures"
import { INSTRUMENTS, getInstrumentMeta } from "@/lib/outcomeMeasures"
import type { OutcomeMeasure } from "@/types/outcomeMeasures"

interface OutcomeMeasuresTabProps {
  patientId: string
}

export function OutcomeMeasuresTab({ patientId }: OutcomeMeasuresTabProps) {
  const { data, isLoading, error } = usePatientOutcomeMeasures(patientId)
  const { showToast } = useToast()
  const deleteMeasure = useDeleteOutcomeMeasure()

  async function handleDelete(measure: OutcomeMeasure) {
    if (
      typeof window !== "undefined" &&
      !window.confirm("Delete this score? It will be removed from the trend.")
    ) {
      return
    }
    try {
      await deleteMeasure.mutateAsync({ measureId: measure.id, patientId })
      showToast("Score deleted.", "success")
    } catch {
      showToast("Could not delete the score. Please try again.", "error")
    }
  }

  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    )
  }

  if (error) {
    return (
      <p className="text-sm text-red-500">
        {error instanceof Error ? error.message : "Failed to load scores."}
      </p>
    )
  }

  const measures = data?.data ?? []

  if (measures.length === 0) {
    return (
      <div className="flex flex-col items-center gap-3 py-8 text-center">
        <Activity className="h-8 w-8 text-neutral-300" />
        <p className="text-sm text-neutral-600">
          No outcome measures recorded yet.
        </p>
        <RecordOutcomeMeasureButton patientId={patientId} />
      </div>
    )
  }

  // Group by instrument, preserving the registry's display order. Unknown
  // instrument codes (e.g. a backend-only addition) still render under their
  // own raw-code heading via a synthesized meta fallback.
  const byInstrument = new Map<string, OutcomeMeasure[]>()
  for (const m of measures) {
    const list = byInstrument.get(m.instrument) ?? []
    list.push(m)
    byInstrument.set(m.instrument, list)
  }

  const orderedCodes = [
    ...INSTRUMENTS.map((i) => i.code).filter((c) => byInstrument.has(c)),
    ...[...byInstrument.keys()].filter(
      (c) => !INSTRUMENTS.some((i) => i.code === c),
    ),
  ]

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <RecordOutcomeMeasureButton patientId={patientId} />
      </div>
      {orderedCodes.map((code) => {
        const meta = getInstrumentMeta(code) ?? {
          code,
          displayName: code.toUpperCase(),
          items: [],
          responseOptions: [],
        }
        return (
          <OutcomeMeasureTrend
            key={code}
            meta={meta}
            measures={byInstrument.get(code) ?? []}
            onDelete={handleDelete}
            deletingId={deleteMeasure.isPending ? deleteMeasure.variables?.measureId : null}
          />
        )
      })}
    </div>
  )
}
